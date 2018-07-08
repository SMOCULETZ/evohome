"""
Support for Honeywell (EU-only) Evohome installations: 1 controller & 1+ zones.

To install it, copy it to ${HASS_CONFIG_DIR}/custom_components. The
configuration.yaml as below.  scan_interval is in seconds, but is rounded up to
nearest minute.

evohome:
  username: !secret_evohome_username
  password: !secret_evohome_password
  scan_interval: 300
"""

# TBD
# re: https://developers.home-assistant.io/docs/en/development_index.html
#  - checked with: flake8 --ignore=E303,E241 --max-line-length=150 evohome.py

import functools as ft
import logging
import requests
import sched
import socket
import voluptuous as vol


from datetime import datetime, timedelta
from time import sleep, strftime, strptime, mktime

from homeassistant.core                import callback
from homeassistant.helpers.discovery   import load_platform
from homeassistant.helpers.temperature import display_temp as show_temp
from homeassistant.helpers.entity      import Entity
from homeassistant.helpers.event       import track_state_change

import homeassistant.helpers.config_validation as cv
# from homeassistant.helpers.config_validation import PLATFORM_SCHEMA  # noqa

from homeassistant.components.climate import (
    ClimateDevice, PLATFORM_SCHEMA,

#   SERVICE_SET_OPERATION_MODE = 'set_operation_mode'
#   SERVICE_SET_TEMPERATURE = 'set_temperature'
#   SERVICE_SET_AWAY_MODE = 'set_away_mode'

    SUPPORT_TARGET_TEMPERATURE,
    SUPPORT_TARGET_TEMPERATURE_HIGH,
    SUPPORT_TARGET_TEMPERATURE_LOW,
    SUPPORT_OPERATION_MODE,
    SUPPORT_AWAY_MODE,
    SUPPORT_ON_OFF,

    ATTR_CURRENT_TEMPERATURE,
    ATTR_MAX_TEMP,
    ATTR_MIN_TEMP,
    ATTR_TARGET_TEMP_HIGH,
    ATTR_TARGET_TEMP_LOW,
    ATTR_TARGET_TEMP_STEP,
    ATTR_OPERATION_MODE,
    ATTR_OPERATION_LIST,
    ATTR_AWAY_MODE,
)

# these are specific to this component
ATTR_UNTIL='until'

from homeassistant.const import (
    CONF_USERNAME, 
    CONF_PASSWORD, 
    CONF_SCAN_INTERVAL,

#   TEMP_FAHRENHEIT,
    TEMP_CELSIUS, 

    PRECISION_WHOLE, 
    PRECISION_HALVES, 
    PRECISION_TENTHS,

#   ATTR_ASSUMED_STATE = 'assumed_state',
#   ATTR_STATE = 'state',
#   ATTR_SUPPORTED_FEATURES = 'supported_features'
#   ATTR_TEMPERATURE = 'temperature'
    ATTR_TEMPERATURE,
)

# these are specific to this component
CONF_HIGH_PRECISION = 'high_precision'
CONF_USE_HEURISTICS = 'use_heuristics'
CONF_USE_SCHEDULES = 'use_schedules'

## TBD: for testing only (has extra logging)
# https://www.home-assistant.io/developers/component_deps_and_reqs/
# https://github.com/home-assistant/home-assistant.github.io/pull/5199

##TBD: these vars for >=0.2.6 (is it v3 of the api?)
#REQUIREMENTS = ['https://github.com/zxdavb/evohome-client/archive/master.zip#evohomeclient==0.2.7'] # noqa
REQUIREMENTS = ['https://github.com/zxdavb/evohome-client/archive/logging.zip#evohomeclient==0.2.7'] # noqa
_SETPOINT_CAPABILITIES = 'setpointCapabilities'
_SETPOINT_STATUS       = 'setpointStatus'
_TARGET_TEMPERATURE    = 'targetHeatTemperature'
_OAUTH_TIMEOUT_SECONDS = 21600  ## TBA: timeout is 6h, client handles oauth

## these vars for <=0.2.5...
#REQUIREMENTS = ['evohomeclient==0.2.5']
#_SETPOINT_CAPABILITIES = 'heatSetpointCapabilities'
#_SETPOINT_STATUS       = 'heatSetpointStatus'
#_TARGET_TEMPERATURE    = 'targetTemperature'
#_OAUTH_TIMEOUT_SECONDS = 3600  ## timeout is 60 mins

## https://www.home-assistant.io/components/logger/
_LOGGER = logging.getLogger(__name__)

DOMAIN='evohome'
DATA_EVOHOME = 'data_evohome'
DISPATCHER_EVOHOME = 'dispatcher_evohome'

# Validation of the user's configuration.
CONFIG_SCHEMA = vol.Schema({
    DOMAIN: vol.Schema({
        vol.Required(CONF_USERNAME): cv.string,
        vol.Required(CONF_PASSWORD): cv.string,
        vol.Optional(CONF_SCAN_INTERVAL, default=180): cv.positive_int,

        vol.Optional(CONF_HIGH_PRECISION, default=True): cv.boolean,
        vol.Optional(CONF_USE_HEURISTICS, default=False): cv.boolean,
        vol.Optional(CONF_USE_SCHEDULES, default=False): cv.boolean,
    }),
}, extra=vol.ALLOW_EXTRA)


# these are for the controller's opmode/state and the zone's state
EVO_RESET      = 'AutoWithReset'
EVO_AUTO       = 'Auto'
EVO_AUTOECO    = 'AutoWithEco'
EVO_AWAY       = 'Away'
EVO_DAYOFF     = 'DayOff'
EVO_CUSTOM     = 'Custom'
EVO_HEATOFF    = 'HeatingOff'
# these are for zones' opmode, and state
EVO_FOLLOW     = 'FollowSchedule'
EVO_TEMPOVER   = 'TemporaryOverride'
EVO_PERMOVER   = 'PermanentOverride'
EVO_OPENWINDOW = 'OpenWindow'
EVO_FROSTMODE  = 'FrostProtect'



def setup(hass, config):
    """Set up a Honeywell evoTouch heating system (1 controller and multiple zones).""" # noqa
    _LOGGER.info("setup(), temp units: %s...", TEMP_CELSIUS)

# Use the evohome-client v2 API (which uses OAuth)
    from evohomeclient2 import EvohomeClient as EvohomeClient

## TBA: Need to add code to exclude US-based systems...

# get the parameters, note that config.get(CONF_USERNAME) doesn't work
    username = config[DOMAIN][CONF_USERNAME]
    password = config[DOMAIN][CONF_PASSWORD]

### Get the timeouts from the configuration. Use DEFAULTs is not provided
# timeout_short = config[DOMAIN].get(CONF_TIMEOUT_SHORT, DEFAULT_TIMEOUT_SHORT)
# timeout_long = config[DOMAIN].get(CONF_TIMEOUT_LONG, DEFAULT_TIMEOUT_LONG)

    hass.data[DATA_EVOHOME] = {}  # without this, KeyError: 'data_evohome'

    hass.data[DATA_EVOHOME]['config'] = dict(config[DOMAIN])
    del hass.data[DATA_EVOHOME]['config']['username']
    del hass.data[DATA_EVOHOME]['config']['password']

# scan_interval is rounded up to nearest 60 seconds
    hass.data[DATA_EVOHOME]['config'][CONF_SCAN_INTERVAL] \
        = (int((config[DOMAIN][CONF_SCAN_INTERVAL] - 1) / 60) + 1) * 60

    _LOGGER.debug("CONFIG: %s", hass.data[DATA_EVOHOME]['config'])


# Do we perform only an update, or a full refresh (incl. OAuth access token)?
    _LOGGER.debug("Connecting to the client (Honeywell web) API...")
    try:  ## client._login() is called by client.__init__()
        _LOGGER.info("Calling v2 API [4 request(s)]: client.__init__()...")
        ec_api = EvohomeClient(username, password, debug=False)
    except:
        _LOGGER.error("Failed to connect to the client (Honeywell web) API!")
        raise
    _LOGGER.debug("Connect OK to the client (Honeywell web) API.")

# The latest evohomeclient uses: requests.exceptions.HTTPError, including:
# - 400 Client Error: Bad Request for url:      [ Bad credentials ]
# - 429 Client Error: Too Many Requests for url [ Limit exceeded ]

    _updateStateData(ec_api, hass.data[DATA_EVOHOME], force_refresh=True)

## Load platforms...
    load_platform(hass, 'climate', DOMAIN)

    _LOGGER.info("Finished: setup()")
    return True


def _updateStateData(evo_client, domain_data, force_refresh=False):

    if force_refresh is True:
        domain_data['evohomeClient'] = evo_client

# OAuth tokens need periodic refresh, but the client exposes no api for that
        timeout = datetime.now() + timedelta(seconds \
            = _OAUTH_TIMEOUT_SECONDS \
            - domain_data['config'][CONF_SCAN_INTERVAL] - 5)

        domain_data['tokenExpires'] = timeout

        _LOGGER.info("OAuth token expires shortly after %s", timeout)

# These are usually updated once per authentication cycle...
        if True:
            domain_data['installation'] \
                = _returnConfiguration(evo_client)
        if domain_data['config'][CONF_USE_SCHEDULES]:
            domain_data['schedule'] \
                = _returnZoneSchedules(
                      evo_client.locations[0]._gateways[0]._control_systems[0]
                  )
#       domain_data['lastRefreshed'] \
#           = datetime.now()

# These are usually updated once per 'scan_interval' cycle...
    if True:
        if domain_data['config'][CONF_HIGH_PRECISION]:
            domain_data['status'] \
                = _returnTempsAndModes(evo_client, high_precision=True)
        else:
            domain_data['status'] \
                = _returnTempsAndModes(evo_client, high_precision=False)

    domain_data['lastUpdated'] = datetime.now()

# Some of this data should be redacted before getting into the logs
    if _LOGGER.isEnabledFor(logging.INFO) and force_refresh is True:
        _tmp = domain_data
        _tmp['installation']['locationInfo']['postcode'] = 'REDACTED'
#       _tmp['schedule'] = {}

        _LOGGER.info("hass.data[DATA_EVOHOME]: %s", _tmp)
        _tmp = ""

    return True


def _returnConfiguration(client, force_update=False):
    _LOGGER.info(
        "_returnConfiguration(client, force_update=%s)",
        force_update
    )

# BUG?: or client.installation_info is Null
    if force_update is True:
        _LOGGER.info(
            "Calling v2 API [? request(s)]: client.installation()..."
        )
        client.installation()

# client.installation_info[0] is more efficient than client.fullInstallation()
    _conf = client.installation_info[0]

# Now redact unneeded info
    _conf['locationInfo']['locationId'] = 'REDACTED'
    _conf['locationInfo']['streetAddress'] = 'REDACTED'
    _conf['locationInfo']['city'] = 'REDACTED'

    _conf['locationInfo']['locationOwner']['userId'] = 'REDACTED'
    _conf['locationInfo']['locationOwner']['username'] = 'REDACTED'
    _conf['locationInfo']['locationOwner']['firstname'] = 'REDACTED'
    _conf['locationInfo']['locationOwner']['lastname'] = 'REDACTED'

    _conf['gateways'][0]['gatewayInfo']['gatewayId'] = 'REDACTED'
    _conf['gateways'][0]['gatewayInfo']['mac'] = 'REDACTED'
    _conf['gateways'][0]['gatewayInfo']['crc'] = 'REDACTED'


### ZX Hack for testing DHW...
    if False:
        _conf['gateways'][0]['temperatureControlSystems'][0]['dhw'] = \
            { \
                "dhwId": "999999", \
                "dhwStateCapabilitiesResponse": { \
                    "allowedStates": [ "On", "Off" ], \
                    "maxDuration": "1.00:00:00", \
                    "timingResolution": "00:10:00", \
                    "allowedModes": [ \
                        "FollowSchedule", \
                        "PermanentOverride", \
                        "TemporaryOverride" ] }, \
                "scheduleCapabilitiesResponse": { \
                    "minSwitchpointsPerDay": 1, \
                    "maxSwitchpointsPerDay": 6, \
                    "timingResolution": "00:10:00" } }
#       _LOGGER.debug("ZX _returnConfiguration() = %s", _conf)
### ZX Hack ends.

    return _conf


def _returnTempsAndModes(client, force_update=False, high_precision=False):
## Get the latest modes/temps (assumes only 1 location/controller)
    _LOGGER.info("_returnTempsAndModes(client)")

#   if force_update is True:
#        _LOGGER.info("Calling v2 API [?x]: client.installation()...")
#       hass.data[DATA_EVOHOME]['installation'] = client.installation()

    _LOGGER.info(
        "Calling v2 API [1 request(s)]: client.locations[0].status()..."
    )
    ec2_status = client.locations[0].status()  # get latest modes/temps
    ec2_tcs = ec2_status['gateways'][0]['temperatureControlSystems'][0]

    _LOGGER.debug("ec2_api.status() = %s", ec2_status)

    if high_precision is True:
        try:
            _LOGGER.debug("Using client v1 API (for higher precision temps)")

            from evohomeclient import EvohomeClient as EvohomeClientVer1  ## uses v1 of the api
            ec1_api = EvohomeClientVer1(client.username, client.password)

            _LOGGER.info("Calling v1 API [2 requests]: client.temperatures()...")
            ec1_temps = ec1_api.temperatures(force_refresh=True)  # is a generator?
            _LOGGER.debug("ev_api.temperatures() = %s", ec1_temps)

            for temp in ec1_temps:
                _LOGGER.debug("Zone %s (%s) reports temp %s", temp['id'], temp['name'], temp['temp'])

                for zone in ec2_tcs['zones']:
                    _LOGGER.debug(" - is it slave %s (%s)?", zone['zoneId'], zone['name'])

                    if str(temp['id']) == str(zone['zoneId']):
                        _LOGGER.debug(" - matched: temp changed from %s to %s.", zone['temperatureStatus']['temperature'], temp['temp'])
                        _LOGGER.debug(" - matched: temp for child %s (%s) changed from %s to %s.", zone['zoneId'], zone['name'], zone['temperatureStatus']['temperature'], temp['temp'])
                        zone['temperatureStatus']['temperature'] = temp['temp']

                        break

        except:
            _LOGGER.error("Unable to increase precision of temps (via the v1 api), ignoring this ERROR")
#           raise

        finally:
#           ec1_api = None  # do I need to clean this up?
            pass


    if _LOGGER.isEnabledFor(logging.DEBUG):
        for zone in ec2_tcs['zones']:
            _LOGGER.debug("update(controller) - for child %s (%s), temp = %s.", zone['zoneId'], zone['name'], zone['temperatureStatus']['temperature'])


### ZX Hack for DHW...
    if False:
        ec2_tcs['dhw'] = \
            { \
                "dhwId": "999999", \
                "stateStatus": { \
                    "state": "On", \
                    "mode": "FollowSchedule" }, \
                "temperatureStatus": { \
                    "temperature": 61, \
                    "isAvailable": True }, \
                "activeFaults": [] }
#       _LOGGER.debug("ZX _returnTempsAndModes() = %s", ec2_tcs)
### ZX Hack ends.

    return ec2_tcs


def _returnZoneSchedules(tcs):
# the client api does not expose a way to do this (it outputs to a file)
    _LOGGER.info("_returnZoneSchedules(tcs=%s)", tcs.systemId)

    schedules = {}

## Collect each (slave) zone as a (climate component) device
## This line requires only 1 location/controller, the next works for 1+
#   for z in client._get_single_heating_system()._zones:

# first, for all/any heating zones
#   for z in client.locations[0]._gateways[0]._control_systems[0]._zones:
    for z in tcs._zones:
        _LOGGER.info("Calling v2 API [1 request(s)]: client.zone.schedule(Zone=%s)...", z.zoneId)
        s = z.schedule()
        schedules[z.zoneId] = {'name': z.name, 'schedule': s}
#       zone_type = temperatureZone, or domesticHotWater
        _LOGGER.info(" - zoneId = %s, zone_type = %s",
            z.zoneId,
            z.zone_type
        )

# then, for any DHW
    if tcs.hotwater:
        z = tcs.hotwater
        _LOGGER.info("Calling v2 API [1 request(s)]: client.zone.schedule(DHW=%s)...", z.zoneId)
        s = z.schedule()
        schedules[z.zoneId] = {'name': z.zone_type, 'schedule': s}
#       zone_type = temperatureZone, or domesticHotWater
        _LOGGER.info(" - zoneId = %s, zone_type = %s",
            z.zoneId,
            z.zone_type
        )

    _LOGGER.info(
        "_returnZoneSchedules() = %s",
        schedules
    )

    return schedules  # client.zone_schedules_backup()



class evoEntity(Entity):
    """Base for Honeywell evohome slave devices (Heating/DHW zones)."""

    def _getZoneById(self, zoneId, dataSource='status'):

        if dataSource == 'schedule':
            _zones = self.hass.data[DATA_EVOHOME]['schedule']

            if zoneId in _zones:
                return _zones[zoneId]
            else:
                raise KeyError("zone '", zoneId, "' not in dataSource")

        if dataSource == 'config':
            _zones = self.hass.data[DATA_EVOHOME]['installation'] \
                ['gateways'][0]['temperatureControlSystems'][0]['zones']

        else:  ## if dataSource == 'status':
            _zones = self.hass.data[DATA_EVOHOME]['status']['zones']

        for _zone in _zones:
            if _zone['zoneId'] == zoneId:
                return _zone
    # or should this be an IndexError?
        
        raise KeyError("Zone not found in dataSource, ID: ", zoneId)


    def _getZoneSchedTemp(self, zoneId, dt=None):

        if dt is None: dt = datetime.now()
        _dayOfWeek = int(dt.strftime('%w'))  ## 0 is Sunday
        _timeOfDay = dt.strftime('%H:%M:%S')

        _sched = self._getZoneById(zoneId, 'schedule')

# start with the last setpoint of yesterday
        for _day in _sched['schedule']['DailySchedules']:
            if _day['DayOfWeek'] == (_dayOfWeek + 6) % 7:
                for _switchPoint in _day['Switchpoints']:
                    if True:
                        _setPoint = _switchPoint['heatSetpoint']

# walk through all of todays setpoints...
        for _day in _sched['schedule']['DailySchedules']:
            if _day['DayOfWeek'] == _dayOfWeek:
                for _switchPoint in _day['Switchpoints']:
                    if _timeOfDay < _switchPoint['TimeOfDay']:
                        _setPoint = _switchPoint['heatSetpoint']
                    else:
                        break

        return _setPoint



class evoControllerEntity(evoEntity):
    """Base for a Honeywell evohome master device (Controller)."""

    def __init__(self, hass, client, controller):
        """Initialize the evohome Controller."""
        self.hass = hass
        self.client = client

        self._id = controller['systemId']
        self._obj = self.client.locations[0]._gateways[0]._control_systems[0]
# not sure if this is right for polled IOT devices
#       self._assumed_state = False

# create a listener for update packets...
        hass.helpers.dispatcher.async_dispatcher_connect(
            DISPATCHER_EVOHOME,
            self._connect
        )  # for: def async_dispatcher_connect(signal, target)

# Ensure to Update immediately after entity has initialized (how?)
        self._should_poll = True

        _LOGGER.info("__init__(TCS=%s)", self._id + " [" + self.name + "]")
        return None

    @callback
    def _connect(self, packet):
        """Process a dispatcher connect."""
        _LOGGER.info(
            "Controller has received a '%s' packet from %s",
            packet['signal'],
            packet['sender']
        )

        if False:
            _LOGGER.info(
                " - Controller is calling self.update()"
            )

            self.update
            self.async_schedule_update_ha_state() # look at force?

        return None

    @property
    def should_poll(self):
        """Controller should TBA. The controller will provide the state data."""
        _LOGGER.info("should_poll(Controller=%s) = %s", self._id, self._should_poll)
        return self._should_poll

    @property
    def force_update(self):
        """Controllers should update when state date is updated, even if it is unchanged."""
        _force = False
        _LOGGER.info("force_update(Controller=%s) = %s", self._id,  _force)
        return _force

    @property
    def name(self):
        """Get the name of the controller."""
        _name = "_" + self.hass.data[DATA_EVOHOME]['installation'] \
            ['locationInfo']['name']
        _LOGGER.debug("name(Controller=%s) = %s", self._id, _name)
        return _name

    @property
    def icon(self):
        """Return the icon to use in the frontend UI."""
        _icon = "mdi:thermostat"
        _LOGGER.debug("icon(Controller=%s) = %s", self._id, _icon)
        return _icon

    @property
    def state(self):
        """Return the controller's current state (usually, its operation mode). After calling AutoWithReset, the controller  will enter Auto mode."""

        _opmode = self.hass.data[DATA_EVOHOME]['status'] \
            ['systemModeStatus']['mode']

        if _opmode == EVO_RESET:
            _LOGGER.info("state(Controller=%s) = %s (from %s)", self._id, EVO_AUTO, _opmode)
            return EVO_AUTO
        else:
            _LOGGER.info("state(Controller=%s) = %s", self._id, _opmode)
            return _opmode

    @property
    def state_attributes(self):
        """Return the optional state attributes."""
        _data = {}

        if self.supported_features & SUPPORT_OPERATION_MODE:
            _data[ATTR_OPERATION_MODE] = self.current_operation
#           _data[ATTR_OPERATION_MODE] = self.hass.data[DATA_EVOHOME] \
#               ['status']['systemModeStatus']['mode']

            _data[ATTR_OPERATION_LIST] = self.operation_list
#           _oplist = []
#           for mode in self.hass.data[DATA_EVOHOME]['installation'] \
#               ['gateways'][0]['temperatureControlSystems'][0]['allowedSystemModes']:
#               _oplist.append(mode['systemMode'])
#           _data[ATTR_OPERATION_LIST] = _oplist

        _LOGGER.info("state_attributes(Controller=%s) = %s",  self._id, _data)
#       return _data


#   @property
#   def device_state_attributes(self):
#       """Return the optional state attributes."""
#       _LOGGER.info("device_state_attributes(Controller=%s)", self._id)
#
#       _data = {}
#
#       _LOGGER.info("device_state_attributes(Controller) = %s", _data)
        return _data

    @property
    def current_operation(self):
        """Return the operation mode of the controller."""

        _opmode = self.hass.data[DATA_EVOHOME]['status'] \
            ['systemModeStatus']['mode']

        _LOGGER.info("current_operation(Controller=%s) = %s", self._id, _opmode)
        return _opmode

    @property
    def operation_list(self):
        """Return the list of available operation modes."""
        _oplist = []
        for mode in self.hass.data[DATA_EVOHOME]['installation'] \
            ['gateways'][0]['temperatureControlSystems'][0]['allowedSystemModes']:
            _oplist.append(mode['systemMode'])

        _LOGGER.info("operation_list(Controller=%s) = %s", self._id, _oplist)
        return _oplist


    def async_set_operation_mode(self, operation_mode):
        """Set new target operation mode. This method must be run in the event loop and returns a coroutine."""
        return self.hass.async_add_job(self.set_operation_mode, operation_mode)


    def set_operation_mode(self, operation_mode):
#   def set_operation_mode(self: ClimateDevice, operation: str) -> None:
        """Set new target operation mode.

        'AutoWithReset may not be a mode in itself: instead, it _should_(?) lead to 'Auto' mode after resetting all the zones to 'FollowSchedule'. How should this be done?

        'Away' mode applies to the controller, not it's (slave) zones.

        'HeatingOff' doesn't turn off heating, instead: it simply sets setpoints to a minimum value (i.e. FrostProtect mode)."""

## For (slave) Zones, when the (master) Controller enters:
# EVO_AUTOECO, it resets EVO_TEMPOVER (but not EVO_PERMOVER) to EVO_FOLLOW
# EVO_DAYOFF,  it resets EVO_TEMPOVER (but not EVO_PERMOVER) to EVO_FOLLOW

## At the start, the first thing to do is stop polled updates() until after
# set_operation_mode() has been called/effected
#       self.hass.data[DATA_EVOHOME]['lastUpdated'] = datetime.now()
        self._should_poll = False

## get the system's current operation mode
        _opmode = self.hass.data[DATA_EVOHOME]['status'] \
            ['systemModeStatus']['mode']

        _LOGGER.info(
            "set_operation_mode(Controller=%s, operation_mode=%s), current mode = %s",
            self._id,
            operation_mode,
            _opmode
        )

# PART 1: call the api & trick the UI
# client.set_status_reset() does not exist only in >0.2.6
        if operation_mode == EVO_RESET:
            _LOGGER.info("Calling v2 API [1 request(s)]: controller._set_status()...")
## This line requires only 1 location/controller, the next works for 1+
#           self.client._get_single_heating_system()._set_status(EVO_AUTO)
            self.client.locations[0]._gateways[0]._control_systems[0]._set_status(EVO_AUTO)

            if False:
                _LOGGER.info(" - updating Zone state data (%s), EVO_RESET")
                _zones = self.hass.data[DATA_EVOHOME]['status']['zones']
                for _zone in _zones:
                    _zone[_SETPOINT_STATUS]['setpointMode'] == EVO_FOLLOW
                    _zone[_SETPOINT_STATUS][_TARGET_TEMPERATURE] \
                        = self._getZoneById(self._id, 'schedule')['name']

        else:
#XX           self._current_operation = operation_mode
# There is no EvohomeClient.set_status_reset via the client v2 API (<=2.6), so
# we're using EvohomeClient._get_single_heating_system()._set_status(5) instead.
## This line requires only 1 location/controller, not 1+
            functions = {
#               EVO_RESET:     self.client.set_status_reset,
                EVO_AUTO:      self.client.set_status_normal,
                EVO_AUTOECO:   self.client.set_status_eco,
                EVO_DAYOFF:    self.client.set_status_dayoff,
                EVO_AWAY:      self.client.set_status_away,
                EVO_HEATOFF:   self.client.set_status_heatingoff,
                EVO_CUSTOM:    self.client.set_status_custom,
            }

# before calling func(), should check OAuth token still viable, but how?
            _func = functions[operation_mode]
            _LOGGER.info(
                "Calling v2 API [1 request(s)]: controller._set_status_%s()...",
                operation_mode
            )
            _func()


## First, Update the state of the Controller
        if True:
            _LOGGER.info(" - updating controller state()")
## Do one of the following (sleep just doesn't work, convergence is too long)...
            if True:
                self.hass.data[DATA_EVOHOME]['status'] \
                    ['systemModeStatus']['mode'] = operation_mode
            else:
                _LOGGER.info(" - sleeping for x seconds...")
                sleep(60)  # allow system to quiesce...
                _LOGGER.info(" - sleep is finished.")
                _updateStateData(self.client, self.hass.data[DATA_EVOHOME])

            _LOGGER.info(" - calling controller.async_schedule_update_ha_state(force_refresh=False)")
##          self.async_update_ha_state(force_refresh=False)           ## doesn't work
##          self.schedule_update_ha_state(force_refresh=False)        ## works
#           self.async_schedule_update_ha_state(force_refresh=False)  ## works
## either of the above cause:
# state()=state, state_attributes()=op_mode+/-temp, supported_features()=128, force_update()=False


## Second, Inform the Zones that their state is now 'assumed'
        if True:
            _packet = {'sender': 'controller', 'signal': 'assume'}
            _LOGGER.info(" - sending a dispatcher packet, %s...", _packet)
## invokes def async_dispatcher_send(hass, signal, *args) on zones:
            self.hass.helpers.dispatcher.async_dispatcher_send(DISPATCHER_EVOHOME, _packet)


## Second, Update target_temp of the Zones
        _LOGGER.info(" - updating Zone state, Controller is '%s'", operation_mode)
        _zones = self.hass.data[DATA_EVOHOME]['status']['zones']

        if operation_mode == EVO_CUSTOM:
            # target temps currently unknowable, await  next update()
            pass

        elif operation_mode == EVO_RESET:
            for _zone in _zones:
                _zone[_SETPOINT_STATUS]['setpointMode'] \
                    = EVO_FOLLOW
            # set target temps according to schedule (if we're using schedules)
                if self.hass.data[DATA_EVOHOME]['config'][CONF_USE_SCHEDULES] \
                    and _zone[_SETPOINT_STATUS]['setpointMode'] == EVO_FOLLOW:
                    _zone[_SETPOINT_STATUS][_TARGET_TEMPERATURE] \
                        = self._getZoneSchedTemp(_zone['zoneId'])

        elif operation_mode == EVO_AUTO:
            for _zone in _zones:
                if _zone[_SETPOINT_STATUS]['setpointMode'] != EVO_PERMOVER:
                    _zone[_SETPOINT_STATUS]['setpointMode'] \
                        = EVO_FOLLOW
            # set target temps according to schedule (if we're using schedules)
                if self.hass.data[DATA_EVOHOME]['config'][CONF_USE_SCHEDULES] \
                    and _zone[_SETPOINT_STATUS]['setpointMode'] == EVO_FOLLOW:
                    _zone[_SETPOINT_STATUS][_TARGET_TEMPERATURE] \
                        = self._getZoneSchedTemp(_zone['zoneId'])

        elif operation_mode == EVO_AUTOECO:
            for _zone in _zones:
                if _zone[_SETPOINT_STATUS]['setpointMode'] != EVO_PERMOVER:
                    _zone[_SETPOINT_STATUS]['setpointMode'] \
                        = EVO_FOLLOW
            # set target temps according to schedule, but less 3
                if self.hass.data[DATA_EVOHOME]['config'][CONF_USE_SCHEDULES] \
                    and _zone[_SETPOINT_STATUS]['setpointMode'] == EVO_FOLLOW:
                    _zone[_SETPOINT_STATUS][_TARGET_TEMPERATURE] \
                        = self._getZoneSchedTemp(_zone['zoneId']) - 3

        elif operation_mode == EVO_DAYOFF:
            for _zone in _zones:
                if _zone[_SETPOINT_STATUS]['setpointMode'] != EVO_PERMOVER:
                    _zone[_SETPOINT_STATUS]['setpointMode'] \
                        = EVO_FOLLOW
            # set target temps according to schedule, but for Saturday
                if self.hass.data[DATA_EVOHOME]['config'][CONF_USE_SCHEDULES] \
                    and _zone[_SETPOINT_STATUS]['setpointMode'] == EVO_FOLLOW:
                    _dt = datetime.now()
                    _dt += timedelta(days = 6 - int(_dt.strftime('%w')))
                    _zone[_SETPOINT_STATUS][_TARGET_TEMPERATURE] \
                        = self._getZoneSchedTemp(_zone['zoneId'], dt)

        elif operation_mode == EVO_AWAY:
            for _zone in _zones:
                if _zone[_SETPOINT_STATUS]['setpointMode'] != EVO_PERMOVER:
                    _zone[_SETPOINT_STATUS]['setpointMode'] \
                        = EVO_FOLLOW
            # default target temps for 'Away' is 10C, assume that for now
                if self.hass.data[DATA_EVOHOME]['config'][CONF_USE_SCHEDULES]:
                    _zone[_SETPOINT_STATUS][_TARGET_TEMPERATURE] \
                        = 10

        elif operation_mode == EVO_HEATOFF:
            for _zone in _zones:
                if _zone[_SETPOINT_STATUS]['setpointMode'] != EVO_PERMOVER:
                    _zone[_SETPOINT_STATUS]['setpointMode'] \
                        = EVO_FOLLOW
            # default target temps for 'HeatingOff' is 5C, assume that for now
                if self.hass.data[DATA_EVOHOME]['config'][CONF_USE_SCHEDULES]:
                    _zone[_SETPOINT_STATUS][_TARGET_TEMPERATURE] \
                        = 5



## Finally, send a message informing Zones that their state may have changed?...
#       self.hass.bus.fire('mode_changed', {ATTR_ENTITY_ID: self._scs_id, ATTR_STATE: command})
        if True:
            _packet = {'sender': 'controller', 'signal': 'update'}
            _LOGGER.info(" - sending a dispatcher packet, %s...", _packet)
## invokes def async_dispatcher_send(hass, signal, *args) on zones:
            self.hass.helpers.dispatcher.async_dispatcher_send(DISPATCHER_EVOHOME, _packet)

## At the end, the last thing to do is restart updates()
        self.hass.data[DATA_EVOHOME]['lastUpdated'] = datetime.now()
        self._should_poll = True

        return None

    @property
    def supported_features(self):
        """Get the list of supported features of the controller."""
## It will likely be the case we need to support Away/Eco/Off modes in the HA fashion
## even though these modes are subtly different - this will allow tight integration
## with the HA landscape / other HA components, e.g. Alexa/Google integration
        _LOGGER.info(
            "supported_features(Controller=%s) = %s",
            self._id,
            SUPPORT_OPERATION_MODE
        )
        return SUPPORT_OPERATION_MODE


    def update(self):
        """Get the latest state (operating mode) of the controller and
        update the state (temp, setpoint) of all children zones.

        Get the latest schedule of the controller every hour."""
        _LOGGER.info("update(Controller=%s)", self._id)

## wait a minimum of scan_interval between updates
        _lastUpdated = self.hass.data[DATA_EVOHOME]['lastUpdated']
        _scanInterval = self.hass.data[DATA_EVOHOME]['config'][CONF_SCAN_INTERVAL]

        if datetime.now() < _lastUpdated + timedelta(seconds = _scanInterval):
            _LOGGER.info(
                "update(Controller=%s) interval timer not expired, exiting",
                self._id
            )
            return

        _LOGGER.info(
            "update(Controller=%s) interval timer expired, proceeding...",
            self._id
        )

## TBA: no provision (yet) for DHW

## If the OAuth token has expired, we need to re-authenticate to get another
        timeout = self.hass.data[DATA_EVOHOME]['tokenExpires']

# Do we perform only an update, or a full refresh (incl. OAuth access token)?
        if datetime.now() > timeout:
            _LOGGER.info("Re-Authenticating as OAuth token (deemed) expired %s", timeout)
            try:  ## client._login()
                _LOGGER.info("Calling v2 API [4 request(s)]: client._login()...")
                self.client.locations = []  ## see: https://github.com/watchforstock/evohome-client/issues/43
                self.client._login()
            except:
                _LOGGER.error("Failed to re-connect to the Honeywell web API!")
                raise

            _updateStateData(self.client, self.hass.data[DATA_EVOHOME], force_refresh=True)
        else:
            _updateStateData(self.client, self.hass.data[DATA_EVOHOME])

# Now send a message to the slaves to update themselves
# store data in hass.data, platforms subscribe with dispatcher_connect, component notifies of updates using dispatch_send
        if True:
            _packet = {'sender': 'controller', 'signal': 'update'}
            _LOGGER.info(" - sending a dispatcher packet, %s...", _packet)
## invokes def async_dispatcher_send(hass, signal, *args) on zones:
            self.hass.helpers.dispatcher.async_dispatcher_send(DISPATCHER_EVOHOME, _packet)

        return True



class evoSlaveEntity(evoEntity):
    """Base for Honeywell evohome slave devices (Heating/DHW zones)."""

    def __init__(self, hass, client, objRef):
        """Initialize the evohome evohome Heating/DHW zone."""
        self.hass = hass
        self.client = client
# this works for DHW, but also objRef.dhwId
        self._id = objRef.zoneId
        self._obj = objRef
# not sure if this is right for polled IOT devices
        self._assumed_state = False

# create a listener for update packets...
        hass.helpers.dispatcher.async_dispatcher_connect(
            DISPATCHER_EVOHOME,
            self._connect
        )  # for: def async_dispatcher_connect(signal, target)

# Ensure to Update immediately after entity has initialized (how?)
#       self._should_poll = False

        _LOGGER.info("__init__(%s)", self._id + " [" + self.name + "]")
        return None  ## should return None

    @property
    def supported_features(self):
        """Return the list of supported features of the Heating/DHW zone."""
        if self._obj.zone_type == 'domesticHotWater':
            _feats = SUPPORT_OPERATION_MODE | SUPPORT_ON_OFF
        else:
            _feats = SUPPORT_OPERATION_MODE | SUPPORT_TARGET_TEMPERATURE

        _LOGGER.debug("supported_features(%s) = %s", self._id, _feats)
        return _feats

    @property
    def operation_list(self):
        """Return the list of operating modes of the Heating/DHW zone."""
# this list is hard-coded fro a particular order
#       if self._obj.zone_type != 'domesticHotWater':
#           _oplist = self._getZoneById(self._id, 'config') \
#               [_SETPOINT_CAPABILITIES]['allowedSetpointModes']
        _oplist = (EVO_FOLLOW, EVO_TEMPOVER, EVO_PERMOVER) # trying...
#       _oplist = [EVO_FOLLOW, EVO_TEMPOVER, EVO_PERMOVER] # this works
        _LOGGER.info("operation_list(%s) = %s", self._id, _oplist)
        return _oplist

    @property
    def current_operation(self):
        """Return the current operating mode of the Heating/DHW zone."""
        if self._obj.zone_type == 'domesticHotWater':
            _opmode = self.hass.data[DATA_EVOHOME]['status']['dhw'] \
                ['stateStatus']['mode']
        else:
            _opmode = self._getZoneById(self._id, 'status') \
                [_SETPOINT_STATUS]['setpointMode']

        _LOGGER.info("current_operation(%s) = %s", self._id, _opmode)
        return _opmode


    def async_set_operation_mode(self, operation_mode):
#   def async_set_operation_mode(self, operation_mode, setpoint=None, until=None):
        """Set new target operation mode.

        This method must be run in the event loop and returns a coroutine.
        """
# Explicitly added, cause I am not sure of impact of adding parameters to this
        _LOGGER.warn(
            "async_set_operation_mode(%s, operation_mode=%s)", 
            self._id, 
            operation_mode
            )
        return self.hass.async_add_job(self.set_operation_mode, operation_mode)

    @property
    def name(self):
        """Return the name to use in the frontend UI."""
        if self._obj.zone_type == 'domesticHotWater':
            _name = '~DHW'
        else:
            _name = self._obj.name

        _LOGGER.debug("name(%s) = %s", self._id, _name)
        return _name

    @property
    def icon(self):
        """Return the icon to use in the frontend UI."""
        if self._obj.zone_type == 'domesticHotWater':
            _icon = "mdi:thermometer"
        else:
            _icon = "mdi:radiator"

        _LOGGER.debug("icon(%s) = %s", self._id, _icon)
        return _icon

    @property
    def current_temperature(self):
        """Return the current temperature of the Heating/DHW zone."""
# TBD: use client's state date rather than hass.data[DATA_EVOHOME]['status']
        if self._obj.zone_type == 'domesticHotWater':
            _status = self.hass.data[DATA_EVOHOME]['status']['dhw']
        else:
            _status = self._getZoneById(self._id, 'status')

        if _status['temperatureStatus']['isAvailable']:
            _temp = _status['temperatureStatus']['temperature']
            _LOGGER.debug("current_temperature(%s) = %s", self._id, _temp)
        else:
            _temp = None
            _LOGGER.warn("current_temperature(%s) - unavailable", self._id)
        return _temp

    @property
    def min_temp(self):
        """Return the minimum setpoint (target temp) of the Heating zone.  
        Setpoints are 5-35C by default, but zones can be further limited."""
# Only applies to Heating zones (SUPPORT_TARGET_TEMPERATURE), not DHW
        if self._obj.zone_type == 'domesticHotWater':
            _temp = None
        else:
            _temp = self._getZoneById(self._id, 'config') \
                [_SETPOINT_CAPABILITIES]['minHeatSetpoint']

        _LOGGER.debug("min_temp(%s) = %s", self._id, _temp)
        return _temp

    @property
    def max_temp(self):
        """Return the maximum setpoint (target temp) of the Heating zone.  
        Setpoints are 5-35C by default, but zones can be further limited."""
# Only applies to Heating zones (SUPPORT_TARGET_TEMPERATURE), not DHW
        if self._obj.zone_type == 'domesticHotWater':
            _temp = None
        else:
            _temp = self._getZoneById(self._id, 'config') \
                [_SETPOINT_CAPABILITIES]['maxHeatSetpoint']

        _LOGGER.debug("max_temp(%s) = %s", self._id, _temp)
        return _temp

    @property
    def target_temperature_step(self):
        """Return the step of setpont (target temp) of the Heating zone."""
# Currently only applies to Heating zones (SUPPORT_TARGET_TEMPERATURE), not DHW
#       _step = self._getZoneById(self._id, 'config') \
#           [_SETPOINT_CAPABILITIES]['valueResolution']
        if self._obj.zone_type == 'domesticHotWater':
            _step = None
        else:
# is usually PRECISION_HALVES
            _step = PRECISION_HALVES

        _LOGGER.debug("target_temperature_step(%s) = %s", self._id,_step)
        return _step

    @property
    def temperature_unit(self):
        """Return the temperature unit to use in the frontend UI."""
        _LOGGER.debug("temperature_unit(%s) = %s", self._id, TEMP_CELSIUS)
        return TEMP_CELSIUS

    @property
    def precision(self):
        """Return the temperature precision to use in the frontend UI."""
        if self._obj.zone_type == 'domesticHotWater':
            _precision = PRECISION_WHOLE
        elif self.hass.data[DATA_EVOHOME]['config'][CONF_HIGH_PRECISION]:
            _precision = PRECISION_TENTHS
        else:
            _precision = PRECISION_HALVES

        _LOGGER.debug("precision(%s) = %s", self._id, _precision)
        return _precision

    @property
    def assumed_state(self):
        """Return True if unable to access real state of the entity."""
# After (say) a controller.set_operation_mode, it will take a while for the
# 1. (invoked) client api call (request.xxx) to reach the web server, 
# 2. web server to send message to the controller
# 3. controller to get message to zones
# 4. controller to send message to web server
# 5. next client api call (every scan_interval)
# in between 1. and 5., should assumed_state be True ??

        _LOGGER.info("assumed_state(%s) = %s", self._id, self._assumed_state)
        return self._assumed_state

    @property
    def should_poll(self):
        """The (master) Controller maintains state data, so (slave) zones should not be polled."""
        _poll = False
        _LOGGER.info("should_poll(%s) = %s", self._id, _poll)
        return _poll

    @property
    def force_update(self):
        """Zones should TBA."""
        _force = False
        _LOGGER.info("force_update(%s) = %s", self._id, _force)
        return _force


    def update(self):
        """Get the latest state data (e.g. temp.) of the Heating/DHW zone."""
# This function is maintained by the Controller, so I am not sure should be 
# done here, if anything. Maybe check object references?
#       ec_status = self.hass.data[DATA_EVOHOME]['status']['dhw']
#       if ec_status is None or ec_status == {}:
#           _LOGGER.error("update(%s) ec_status = {}")
#       else:
#           _LOGGER.debug("update(%s) ec_status = %s", ec_status)
        return True

    @callback
    def _connect(self, packet):
        """Process a dispatcher connect."""
        _LOGGER.info(
            "%s has received a '%s' packet from %s",
            self._id + " [" + self.name + "]",
            packet['signal'],
            packet['sender']
        )

        if packet['signal'] == 'update':
            _LOGGER.info(
                "%s is calling schedule_update_ha_state(force_refresh=True)...",
                self._id + " [" + self.name + "]"
            )
#           self.update()
            self.async_schedule_update_ha_state(force_refresh=True)
            self._assumed_state = False

        if packet['signal'] == 'assume':
            # _LOGGER.info(
                # "%s is calling schedule_update_ha_state(force_refresh=False)...",
                # self._id + " [" + self.name + "]"
            # )
            self._assumed_state = True
            self.async_schedule_update_ha_state(force_refresh=False)

        return None



class evoZoneEntity(evoSlaveEntity, ClimateDevice):
    """Base for a Honeywell evohome Heating zone (aka Zone)."""

    @property
    def state(self):
        """Return the zone's current state (usually, its operation mode).

        A zone's state is usually its operation mode, but they may enter
        OpenWindowMode autonomously."""

        _state = None

        _cont_opmode = self.hass.data[DATA_EVOHOME]['status'] \
            ['systemModeStatus']['mode']


# 1: Basic heuristics...
        if self.hass.data[DATA_EVOHOME]['config'][CONF_USE_HEURISTICS]:
            if _cont_opmode == EVO_AWAY:    _state = EVO_AWAY      #(& target_temp = 10)
            if _cont_opmode == EVO_HEATOFF: _state = EVO_FROSTMODE #(& target_temp = 5)

            _LOGGER.warn("state(Zone=%s) = %s (using heuristics)", self._id, _state)


        _zone = self._getZoneById(self._id, 'status')

        _zone_target = _zone[_SETPOINT_STATUS][_TARGET_TEMPERATURE]
        _zone_opmode = _zone[_SETPOINT_STATUS]['setpointMode']

        
# 2: Heuristics for OpenWindow mode...
        if _zone_target == 5 and _state is None:
### TBA do I need to check if zone is in 'FollowSchedule' mode
            if _cont_opmode == EVO_HEATOFF:
                _state = EVO_FROSTMODE
            else:
#               if _zone_opmode == EVO_FOLLOW:
#                   if sched_temp = 5:
#                       _state = _zone_opmode
                _state = EVO_OPENWINDOW

            _LOGGER.info("state(Zone=%s) = %s (latest actual)", self._id, _state)

                
# 3: If we haven't yet figured out the zone's state, then it must be one of:
        if _state is None:
            if _zone_opmode == EVO_FOLLOW:
                if _cont_opmode == EVO_RESET:
                    _state = EVO_AUTO
                elif _cont_opmode == EVO_HEATOFF:
                    _state = EVO_FROSTMODE
                else:
                    _state = _cont_opmode
            else:
                _state = _zone_opmode

            _LOGGER.info("state(Zone=%s) = %s (latest actual)", self._id, _state)


# 4: Otherwise, the Zone's state is equal to as it's current operating mode
        if _state is None:
            _state = _zone_opmode
            _LOGGER.info("state(Zone=%s) = %s (latest actual)", self._id, _state)

        _LOGGER.debug(
            "state(Zone=%s) = %s [setpoint=%s, opmode=%s, cont_opmode=%s]",
            self._id + " [" + self.name + "]",
            _state,
            _zone_target,
            _zone_opmode,
            _cont_opmode,
        )
        return _state

    @property
    def state_attributes(self):
        """Return the optional state attributes."""
        data = {
            ATTR_CURRENT_TEMPERATURE: show_temp(
                self.hass, self.current_temperature, self.temperature_unit,
                self.precision),
            ATTR_MIN_TEMP: show_temp(
                self.hass, self.min_temp, self.temperature_unit,
                self.precision),
            ATTR_MAX_TEMP: show_temp(
                self.hass, self.max_temp, self.temperature_unit,
                self.precision),
            ATTR_TEMPERATURE: show_temp(
                self.hass, self.target_temperature, self.temperature_unit,
                self.precision),
        }

        supported_features = self.supported_features
        if self.target_temperature_step is not None:
            data[ATTR_TARGET_TEMP_STEP] = self.target_temperature_step

        if supported_features & SUPPORT_TARGET_TEMPERATURE_HIGH:
            data[ATTR_TARGET_TEMP_HIGH] = show_temp(
                self.hass, self.target_temperature_high, self.temperature_unit,
                self.precision)

        if supported_features & SUPPORT_TARGET_TEMPERATURE_LOW:
            data[ATTR_TARGET_TEMP_LOW] = show_temp(
                self.hass, self.target_temperature_low, self.temperature_unit,
                self.precision)

        if supported_features & SUPPORT_OPERATION_MODE:
            data[ATTR_OPERATION_MODE] = self.current_operation
            if self.operation_list:
                data[ATTR_OPERATION_LIST] = self.operation_list

        if supported_features & SUPPORT_AWAY_MODE:
            is_away = self.is_away_mode_on
            data[ATTR_AWAY_MODE] = STATE_ON if is_away else STATE_OFF

        _LOGGER.info("state_attributes(Zone=%s) = %s", self._id, data)
        return data

    @property
    def _sched_temperature(self, datetime=None):
        """Return the temperature we try to reach."""
        _temp = self._getZoneById(self._id, 'schedule')

        _LOGGER.debug(
            "_sched_temperature(Zone=%s) = %s", 
            self._id, 
            _temp
        )

    @property
    def target_temperature(self):
        """Return the temperature we try to reach."""

        _temp = None

        if self.hass.data[DATA_EVOHOME]['config'][CONF_USE_HEURISTICS]:
        # get the system's current operating mode
            _opmode = self.hass.data[DATA_EVOHOME]['status'] \
                ['systemModeStatus']['mode']

            if _opmode == EVO_HEATOFF:
                _temp = 5
            elif _opmode == EVO_AWAY:
                _temp = 10

            _LOGGER.info(
                "target_temperature(Zone=%s) = %s (using heuristics)",
                self._id + " [" + self.name + "]",
                _temp
            )

        if _temp is None:
            _temp = self._getZoneById(self._id, 'status') \
                [_SETPOINT_STATUS][_TARGET_TEMPERATURE]

            _LOGGER.info(
                "target_temperature(Zone=%s) = %s (latest actual)",
                self._id + " [" + self.name + "]",
                _temp
            )

        return _temp


    def set_operation_mode(self, operation_mode, setpoint=None, until=None):
#   def set_operation_mode(self: ClimateDevice, operation: str, setpoint=None, until=None) -> None:
        """Set the operating mode for the zone."""
        _LOGGER.info(
            "set_operation_mode(Zone=%s, OpMode=%s, SetPoint=%s, Until=%s)",
            self._id + " [" + self.name + "]",
            operation_mode,
            setpoint,
            until
        )

#      _LOGGER.debug("for Zone=%s: set_operation_mode(operation_mode=%s, setpoint=%s, until=%s)", self.name, operation_mode, setpoint, until)

## This line requires only 1 location/controller, the next works for 1+
#       zone = self.client._get_single_heating_system().zones_by_id([self._id])
        zone = self.client.locations[0]._gateways[0]._control_systems[0].zones_by_id([self._id])

        _zone = self._getZoneById(self._id, 'status')
        _target_temperature = _zone[_SETPOINT_STATUS][_TARGET_TEMPERATURE]
#XX     _current_operation  = _zone[_SETPOINT_STATUS]['setpointMode']

        if operation_mode == EVO_FOLLOW:
            _LOGGER.debug("Calling v2 API [? request(s)]: zone.cancel_temp_override()...",)
            zone.cancel_temp_override(zone)
            setpoint = self._getZoneSchedTemp(_zone['zoneId'], datetime.now())  ## Throws: KeyError: ("zone '", '3449703', "' not in dataSource")

        else:
            if setpoint is None:
                setpoint = _zone[_SETPOINT_STATUS][_TARGET_TEMPERATURE]

        if operation_mode == EVO_PERMOVER:
            _LOGGER.debug("Calling v2 API [? request(s)]: zone.set_temperature(%s)...", setpoint)
            zone.set_temperature(setpoint)  ## override target temp indefinitely

# TBA this code is wrong ...
        if operation_mode == EVO_TEMPOVER:
            if until == None:
# UTC_OFFSET_TIMEDELTA = datetime.now() - datetime.utcnow()
                until = datetime.now() + timedelta(1/24) ## use .utcnow() or .now() ??
            _LOGGER.debug("Calling v2 API [? request(s)]: zone.set_temperature(%s, %s)...", setpoint, until)
            zone.set_temperature(setpoint, until)  ## override target temp (for a hour)

        _LOGGER.debug("Action completed, updating internal state data...")
        _zone[_SETPOINT_STATUS]['setpointMode'] = operation_mode
        _zone[_SETPOINT_STATUS][_TARGET_TEMPERATURE] = setpoint

        _LOGGER.debug(" - calling: controller.schedule_update_ha_state()")
        self.async_schedule_update_ha_state(force_refresh=False)

        return True


    def set_temperature(self, **kwargs):
        """Set a target temperature (setpoint) for the zone."""
        _LOGGER.info(
            "set_temperature(Zone=%s, **kwargs)",
            self._id + " [" + self.name + "]"
        )

#       for name, value in kwargs.items():
#           _LOGGER.debug('%s = %s', name, value)

        _temperature = kwargs.get(ATTR_TEMPERATURE)

        if _temperature is None:
#           _LOGGER.error("set_temperature(temperature=%s) is None!", _temperature)
            return False

        _zone = self._getZoneById(self._id, 'config')

        _max_temp = _zone[_SETPOINT_CAPABILITIES]['maxHeatSetpoint']
        if _temperature > _max_temp:
            _LOGGER.error(
                "set_temperature(temperature=%s) is above maximum, %s!", 
                _temperature,
                _max_temp
            )
            return False

        _min_temp = _zone[_SETPOINT_CAPABILITIES]['minHeatSetpoint']
        if _temperature < _min_temp:
            _LOGGER.error(
                "set_temperature(temperature=%s) is below minimum, %s!", 
                _temperature,
                _min_temp
            )
            return False

        _until = kwargs.get(ATTR_UNTIL)
#       _until = None  ## TBA
        _LOGGER.info("Calling API: zone.set_temperature(temp=%s, until=%s)...", _temperature, _until)

        self._obj.set_temperature(_temperature, _until)

# TBA: first update hass.data[DOMAIN]...
        if self.hass.data[DATA_EVOHOME]['config'][CONF_USE_HEURISTICS]:
            for zone in self.hass.data[DATA_EVOHOME]['status']['zones']:
                if zone['zoneId'] == self._id:
                    zone[_SETPOINT_STATUS][_TARGET_TEMPERATURE] = _temperature
                    if _until is None:
                        zone[_SETPOINT_STATUS]['setpointMode'] = "PermanentOverride"
                    else:
                        zone[_SETPOINT_STATUS]['setpointMode'] = "TemporaryOverride"

# then tell HA that things have changed...
#       self.schedule_update_ha_state()
        return True



class evoDhwEntity(evoSlaveEntity):
    """Base for a Honeywell evohome DHW zone (aka DHW)."""

    @property
    def state(self):
        """Return the current state of the DHW (On, or Off)."""
        _state = None

        if self.hass.data[DATA_EVOHOME]['config'][CONF_USE_HEURISTICS]:
            _cont_opmode = self.hass.data[DATA_EVOHOME]['status'] \
                ['systemModeStatus']['mode']

            if _cont_opmode == EVO_AWAY:    _state = 'Off'
#           if _cont_opmode == EVO_HEATOFF: _state = ???

            _LOGGER.warn("state(DHW=%s) = %s (using heuristics)", self._id, _state)

# if we haven't yet figured out the zone's state, then:
        if _state is None:
            _state = self.hass.data[DATA_EVOHOME]['status']['dhw'] \
                ['stateStatus']['state']

            _LOGGER.info("state(DHW=%s) = %s (latest actual)", self._id, _state)
        return _state


    @property
    def state_attributes(self):
        """Return the optional state attributes."""
# The issue with HA's state_attributes() is that is assumes Climate objects 
# have a:
# - self.current_temperature:      True for Heating & DHW zones
# - self.target_temperature:       True for DHW zones only
# - self.min_temp & self.max_temp: True for DHW zones only

# so we have...
        _data = {
            ATTR_CURRENT_TEMPERATURE: show_temp(
                self.hass, self.current_temperature, self.temperature_unit,
                self.precision),
        }

        _LOGGER.info(
            "state_attributes(DHW=%s) = %s, %s, %s", 
            self._id, 
            self.current_temperature,
            self.temperature_unit,
            self.precision
        )

        supported_features = self.supported_features
        
        if supported_features & SUPPORT_OPERATION_MODE:
            _data[ATTR_OPERATION_MODE] = self.current_operation
            _data[ATTR_OPERATION_LIST] = self.operation_list
            
        if supported_features & SUPPORT_ON_OFF:
#           _data[ATTR_OPERATION_MODE] = self.current_operation
#           _data[ATTR_OPERATION_LIST] = self.operation_list
            pass

        _LOGGER.info("state_attributes(DHW=%s) = %s", self._id, _data)
        return _data


    def set_operation_mode(self, operation_mode):
        """Set new operation mode."""
        if operation_mode == EVO_FOLLOW:
            _state = ''
        else:
            _state = self.state
            
        if operation_mode == EVO_TEMPOVER:
            _until = datetime.now() + timedelta(hours=1)
        else:
            _until = None

        _data =  {'State':_state, 'Mode':operation_mode, 'UntilTime':_until}
        self._obj._set_dhw(_data)
        return


    def async_turn_on(self):
        """Turn device on.

        This method must be run in the event loop and returns a coroutine.
        """
        return self.hass.async_add_job(self.turn_on)


    def turn_on(self):
        """Turn DHW on for an hour."""
        _hour = datetime.now() + timedelta(hours=1)
        _data =  {'State':'On', 'Mode':EVO_TEMPOVER, 'UntilTime':_hour}
        self._obj._set_dhw(_data)
        return


    def async_turn_off(self):
        """Turn device off.

        This method must be run in the event loop and returns a coroutine.
        """
        return self.hass.async_add_job(self.turn_off)


    def turn_off(self):
        """Turn DHW off for an hour."""
        _hour = datetime.now() + timedelta(hours=1)
        _data =  {'State':'Off', 'Mode':EVO_TEMPOVER, 'UntilTime':_hour}
        self._obj._set_dhw(_data)
        return



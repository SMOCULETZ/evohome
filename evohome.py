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
# regarding: https://developers.home-assistant.io/docs/en/development_index.html
#  - checked with: flake8 --ignore=E303,E241 --max-line-length=150 evohome.py



import logging
import socket
import sched
import functools as ft

from datetime import datetime, timedelta
from time import sleep, strftime, strptime, mktime

import requests
import voluptuous as vol

from homeassistant.core              import callback
from homeassistant.helpers.discovery import load_platform
from homeassistant.helpers.entity    import Entity
from homeassistant.helpers.event     import track_state_change

import homeassistant.helpers.config_validation as cv
# from homeassistant.helpers.config_validation import PLATFORM_SCHEMA  # noqa

from homeassistant.components.climate import (
    ClimateDevice, PLATFORM_SCHEMA,

#   SERVICE_SET_OPERATION_MODE = 'set_operation_mode'
#   SERVICE_SET_TEMPERATURE = 'set_temperature'
#   SERVICE_SET_AWAY_MODE = 'set_away_mode'

    SUPPORT_TARGET_TEMPERATURE,
#   SUPPORT_TARGET_TEMPERATURE_HIGH,
#   SUPPORT_TARGET_TEMPERATURE_LOW,
    SUPPORT_OPERATION_MODE,
#   SUPPORT_AWAY_MODE,

#   ATTR_CURRENT_TEMPERATURE = 'current_temperature'
#   ATTR_MAX_TEMP = 'max_temp'
#   ATTR_MIN_TEMP = 'min_temp'
#   ATTR_TARGET_TEMP_HIGH = 'target_temp_high'
#   ATTR_TARGET_TEMP_LOW = 'target_temp_low'
#   ATTR_TARGET_TEMP_STEP = 'target_temp_step'
#   ATTR_OPERATION_MODE = 'operation_mode'
    ATTR_OPERATION_MODE,
#   ATTR_OPERATION_LIST = 'operation_list'
    ATTR_OPERATION_LIST,
#   ATTR_AWAY_MODE = 'away_mode'
    )

ATTR_UNTIL='until'

from homeassistant.const import (
    CONF_USERNAME, CONF_PASSWORD, CONF_SCAN_INTERVAL,
    TEMP_CELSIUS, TEMP_FAHRENHEIT,
    PRECISION_HALVES, PRECISION_TENTHS,
#   ATTR_ASSUMED_STATE = 'assumed_state',
#   ATTR_STATE = 'state',
#   ATTR_SUPPORTED_FEATURES = 'supported_features'
#   ATTR_TEMPERATURE = 'temperature'
    ATTR_TEMPERATURE,

    )

CONF_HIGH_PRECISION = 'high_precision'
CONF_USE_SCHEDULES = 'use_schedules'

## https://www.home-assistant.io/developers/component_deps_and_reqs/
#  https://github.com/home-assistant/home-assistant.github.io/pull/5199

## these vars for >=0.2.6 (is it v3 of the api?)
#REQUIREMENTS = ['https://github.com/zxdavb/evohome-client/archive/master.zip#evohomeclient==0.2.7']
REQUIREMENTS = ['https://github.com/zxdavb/evohome-client/archive/logging.zip#evohomeclient==0.2.7']
_SETPOINT_CAPABILITIES = 'setpointCapabilities'
_SETPOINT_STATUS       = 'setpointStatus'
_TARGET_TEMPERATURE    = 'targetHeatTemperature'
_OAUTH_TIMEOUT_SECONDS = 18000  ## timeout is 30 mins, but client handles that...

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
        vol.Optional(CONF_USE_SCHEDULES, default=True): cv.boolean,
        vol.Optional(CONF_HIGH_PRECISION, default=True): cv.boolean,
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
    """Set up a Honeywell evoTouch heating system (1 controller and multiple zones)."""
    _LOGGER.info("setup(), temp units: %s...", TEMP_CELSIUS)

# Use the evohome-client v2 API (which uses OAuth)
    from evohomeclient2 import EvohomeClient as EvohomeClient

## TBA: Need to add code to exclude US-based systems...

## Establish a connection with the Honeywell api via the client...
    username = config[DOMAIN][CONF_USERNAME]  # config.get(CONF_USERNAME) doesn't work
    password = config[DOMAIN][CONF_PASSWORD]

### Get the timeouts from the configuration. Use DEFAULTs is not provided
#   timeout_short = config[DOMAIN].get(CONF_TIMEOUT_SHORT, DEFAULT_TIMEOUT_SHORT)
#   timeout_long = config[DOMAIN].get(CONF_TIMEOUT_LONG, DEFAULT_TIMEOUT_LONG)

    hass.data[DATA_EVOHOME] = {}  ## without this, KeyError: 'data_evohome'
    
    hass.data[DATA_EVOHOME][CONF_SCAN_INTERVAL] \
        = (int(config[DOMAIN][CONF_SCAN_INTERVAL] / 60) + 1) * 60  ## rounded up to nearest minute
    hass.data[DATA_EVOHOME][CONF_USE_SCHEDULES] \
        =  config[DOMAIN][CONF_USE_SCHEDULES]
    hass.data[DATA_EVOHOME][CONF_HIGH_PRECISION] \
        = config[DOMAIN][CONF_HIGH_PRECISION]
        
    _LOGGER.debug(
        "Scan interval is %s secs, Use schedules: %s, High precision: %s",
        hass.data[DATA_EVOHOME][CONF_SCAN_INTERVAL],
        hass.data[DATA_EVOHOME][CONF_USE_SCHEDULES],
        hass.data[DATA_EVOHOME][CONF_HIGH_PRECISION],
        )

# Do we perform only an update, or a full refresh (incl. OAuth access token)?
    if True:  ## always a full refresh in setup()
        _LOGGER.info("Authenticating for first OAuth token")
        try:  ## client._login() is called by client.__init__()
            _LOGGER.info("Calling client v2 API [4 request(s)]: client.__init__()...")
            ec_api = EvohomeClient(username, password, debug=False)
        except:
            _LOGGER.error("Failed to connect to the Honeywell web API!")
            raise

    _updateStateData(ec_api, hass.data[DATA_EVOHOME], True)
#   _LOGGER.info("Access token expires: %s", ec_api.access_token_expires) only for latest ver of ec
## Load platforms...
    load_platform(hass, 'climate', DOMAIN)

    _LOGGER.info("Finished: setup()")
    return True


def _updateStateData(evo_client, domain_data, force_refresh = False):

    if force_refresh is True:
        domain_data['evohomeClient'] = evo_client

# OAuth tokens need periodic refresh, but the client exposes no api for that
        timeout = datetime.now() + timedelta(seconds \
            = _OAUTH_TIMEOUT_SECONDS - domain_data[CONF_SCAN_INTERVAL] - 5)

        domain_data['tokenExpires'] = timeout

        _LOGGER.info("OAuth token expires shortly after %s", timeout)

# These are usually updated once per authentication cycle...
        if True:
            domain_data['installation'] \
                = _returnConfiguration(evo_client)
        if domain_data[CONF_USE_SCHEDULES] is True: 
            domain_data['schedule'] \
                = _returnZoneSchedules(evo_client)
#       domain_data['lastRefreshed'] \
#           = datetime.now()

# These are usually updated once per 'scan_interval' cycle...
    if True:
        if domain_data[CONF_HIGH_PRECISION] is True:
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
## client.installation_info[0] is more efficient than client.fullInstallation()
    _LOGGER.info(
        "_returnConfiguration(client, force_update=%s)",
        force_update
        )

    if force_update is True: # BUG: or client.installation_info = Null
        _LOGGER.info(
            "Calling client v2 API [? request(s)]: client.installation()..."
            )
        client.installation()           # this will cause a new call, and...

    _LOGGER.info(
        "Calling client v2 API [0 request(s)]: client.installation_info[0]..."
        )
    _conf = client.installation_info[0] # this attribute is updated by that call

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
    
    
### ZX Hack for DHW...
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


def _returnTempsAndModes(client, force_update = False, high_precision = False):
## Get the latest modes/temps (assumes only 1 location/controller)
    _LOGGER.info("_returnTempsAndModes(client)")

#   if force_update is True:
#        _LOGGER.info("Calling client v2 API [?x]: client.installation()...")
#       hass.data[DATA_EVOHOME]['installation'] = client.installation()

    _LOGGER.info(
        "Calling client v2 API [1 request(s)]: client.locations[0].status()..."
        )
    ec2_status = client.locations[0].status()  # get latest modes/temps
    ec2_tcs = ec2_status['gateways'][0]['temperatureControlSystems'][0]

    _LOGGER.debug("ec2_api.status() = %s", ec2_status)

    if high_precision is True:
        try:
            _LOGGER.debug("Using client v1 API (for higher precision temps)")

            from evohomeclient import EvohomeClient as EvohomeClientVer1  ## uses v1 of the api
            ec1_api = EvohomeClientVer1(client.username, client.password)

            _LOGGER.info("Calling client v1 API [2 requests]: client.temperatures(force_refresh = True)...")
# is: _populate_user_info [1x] & _populate_full_data() [1x], if request limit exceeded:
# requests.exceptions.HTTPError: 429 Client Error: Too Many Requests for url: https://tccna.honeywell.com/WebAPI/api/Session
            ec1_temps = ec1_api.temperatures(force_refresh = True)  # is a generator?
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


def _returnZoneSchedules(client):
# the client api does not expose a way to do this (it outputs to a file)
    _LOGGER.info("_returnZoneSchedules(client)")

    schedules = {}

## Collect each (slave) zone as a (climate component) device
    for z in client._get_single_heating_system()._zones:
        _LOGGER.info("Calling client v2 API [1 request(s)]: client.zone.schedule(%s)...", z.zoneId)
        s = z.schedule()
        schedules[z.zoneId] = {'name': z.name, 'schedule': s}
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
    """Honeywell evohome Entity base."""

    def __init__(self, hass, client, device=None):
        """Initialize the evoEntity."""
        self.hass = hass
        self.client = client

        return None  ## should return None



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
        raise KeyError("Zone ID '%s' not found in dataSource", zoneId)



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
    """Honeywell evohome Controller Entity base."""

    def __init__(self, hass, client, controller):
        """Initialize the evoEntity."""
        super().__init__(hass, client, controller)

        self._id = controller['systemId']
#       self._name = "_" + controller['modelType']  # named so is first in list

        _LOGGER.info("__init__(Controller=%s)", self._id)

# listen for update packets...
        hass.helpers.dispatcher.async_dispatcher_connect(
            DISPATCHER_EVOHOME,
            self._connect
        )  # for: def async_dispatcher_connect(signal, target)

        self._should_poll = True
# Process updates in parallel???
#       parallel_updates = True

# Update immediately after entity has initialized -how?

        return None



    @callback
    def _connect(self, packet):
        """Process a dispatcher connect."""
        _LOGGER.info(
            "ZZ Controller has received a '%s' packet from %s",
            packet['signal'],
            packet['sender']
        )

        if False:
            _LOGGER.info(
                "ZZ  - Controller is calling self.update()"
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
# client.set_status_reset does not exist in <=0.2.6
        if operation_mode == EVO_RESET:
            _LOGGER.info("Calling client v2 API [1 request(s)]: controller._set_status()...")
            self.client._get_single_heating_system()._set_status(EVO_AUTO)

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
            func = functions[operation_mode]
            _LOGGER.info(
                "Calling client v2 API [1 request(s)]: controller._set_status_%s()...",
                operation_mode
                )
            func()


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
                if self.hass.data[DATA_EVOHOME][CONF_USE_SCHEDULES] \
                    and _zone[_SETPOINT_STATUS]['setpointMode'] == EVO_FOLLOW:
                    _zone[_SETPOINT_STATUS][_TARGET_TEMPERATURE] \
                        = self._getZoneSchedTemp(_zone['zoneId'])

        elif operation_mode == EVO_AUTO:
            for _zone in _zones:
                if _zone[_SETPOINT_STATUS]['setpointMode'] != EVO_PERMOVER:
                    _zone[_SETPOINT_STATUS]['setpointMode'] \
                        = EVO_FOLLOW
            # set target temps according to schedule (if we're using schedules)
                if self.hass.data[DATA_EVOHOME][CONF_USE_SCHEDULES] \
                    and _zone[_SETPOINT_STATUS]['setpointMode'] == EVO_FOLLOW:
                    _zone[_SETPOINT_STATUS][_TARGET_TEMPERATURE] \
                        = self._getZoneSchedTemp(_zone['zoneId'])

        elif operation_mode == EVO_AUTOECO:
            for _zone in _zones:
                if _zone[_SETPOINT_STATUS]['setpointMode'] != EVO_PERMOVER:
                    _zone[_SETPOINT_STATUS]['setpointMode'] \
                        = EVO_FOLLOW
            # set target temps according to schedule, but less 3
                if self.hass.data[DATA_EVOHOME][CONF_USE_SCHEDULES] \
                    and _zone[_SETPOINT_STATUS]['setpointMode'] == EVO_FOLLOW:
                    _zone[_SETPOINT_STATUS][_TARGET_TEMPERATURE] \
                        = self._getZoneSchedTemp(_zone['zoneId']) - 3

        elif operation_mode == EVO_DAYOFF:
            for _zone in _zones:
                if _zone[_SETPOINT_STATUS]['setpointMode'] != EVO_PERMOVER:
                    _zone[_SETPOINT_STATUS]['setpointMode'] \
                        = EVO_FOLLOW
            # set target temps according to schedule, but for Saturday
                if self.hass.data[DATA_EVOHOME][CONF_USE_SCHEDULES] \
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
                if self.hass.data[DATA_EVOHOME][CONF_USE_SCHEDULES]: 
                    _zone[_SETPOINT_STATUS][_TARGET_TEMPERATURE] \
                        = 10

        elif operation_mode == EVO_HEATOFF:
            for _zone in _zones:
                if _zone[_SETPOINT_STATUS]['setpointMode'] != EVO_PERMOVER:
                    _zone[_SETPOINT_STATUS]['setpointMode'] \
                        = EVO_FOLLOW
            # default target temps for 'HeatingOff' is 5C, assume that for now
                if self.hass.data[DATA_EVOHOME][CONF_USE_SCHEDULES]: 
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
    def state_attributes(self):
        """Return the optional state attributes."""
        _data = {}

#       if self.supported_features & SUPPORT_OPERATION_MODE:
        if True and SUPPORT_OPERATION_MODE:
#           _data[ATTR_OPERATION_MODE] = self.current_operation
            _data[ATTR_OPERATION_MODE] = self.hass.data[DATA_EVOHOME] \
                ['status']['systemModeStatus']['mode']

##          _data[ATTR_OPERATION_LIST] = self.operation_list
            _oplist = []
            for mode in self.hass.data[DATA_EVOHOME]['installation'] \
                ['gateways'][0]['temperatureControlSystems'][0]['allowedSystemModes']:
                _oplist.append(mode['systemMode'])
            _data[ATTR_OPERATION_LIST] = _oplist

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
        _scanInterval = self.hass.data[DATA_EVOHOME][CONF_SCAN_INTERVAL]

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
                _LOGGER.info("Calling client v2 API [4 request(s)]: client._login()...")
                self.client.locations = []  ## see: https://github.com/watchforstock/evohome-client/issues/43
                self.client._login()
            except:
                _LOGGER.error("Failed to re-connect to the Honeywell web API!")
                raise

            _updateStateData(self.client, self.hass.data[DATA_EVOHOME], True)
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



class evoZoneEntity(evoEntity, ClimateDevice):
    """Honeywell evohome Zone Entity base."""

    def __init__(self, hass, client, zone):
        """Initialize the evoEntity."""
        super().__init__(hass, client, zone)

        self._id = zone['zoneId']
        self._name = zone['name']  ## TBA - remove this??

        _LOGGER.info("__init__(zone=%s)", self._id + " [" + self._name + "]")

        self._assumed_state = False

# listen for update packets...
        hass.helpers.dispatcher.async_dispatcher_connect(
            DISPATCHER_EVOHOME,
            self._connect
        )  # for: def async_dispatcher_connect(signal, target)

        return None


    @callback
    def _connect(self, packet):
        """Process a dispatcher connect."""
        _LOGGER.info(
            "ZZ Zone %s has received a '%s' packet from %s",
            self._id + " [" + self._name + "]",
            packet['signal'],
            packet['sender']
        )

        if packet['signal'] == 'update':
            _LOGGER.info(
                "ZZ  - Zone %s is calling schedule_update_ha_state(force_refresh=True)...",
                self._id + " [" + self._name + "]"
            )
#           self.update()
            self.async_schedule_update_ha_state(force_refresh=True)
            self._assumed_state = False

        if packet['signal'] == 'assume':
            # _LOGGER.info(
                # "ZZ  - Zone %s is calling schedule_update_ha_state(force_refresh=False)...",
                # self._id + " [" + self._name + "]"
            # )
            self._assumed_state = True
            self.async_schedule_update_ha_state(force_refresh=False)

        return None


    @property
    def assumed_state(self):
        """Return True if unable to access real state of the entity."""
        _LOGGER.info("assumed_state(Zone=%s) = %s", self._id, self._assumed_state)
        return self._assumed_state


    @property
    def should_poll(self):
        """Zones should not be polled?, the controller will maintain state data."""
        _poll = False
        _LOGGER.info("should_poll(Zone=%s) = %s", self._id, _poll)
        return _poll


    @property
    def force_update(self):
        """Zones should TBA."""
        _force = True
        _LOGGER.info("force_update(Zone=%s) = %s", self._id, _force)
        return _force


    @property
    def state(self):
        """Return the zone's current state (usually, its operation mode).

        A zone's state is usually its operation mode, but they may enter
        OpenWindowMode autonomously."""

        _zone = self._getZoneById(self._id, 'status')

        _zone_target = _zone[_SETPOINT_STATUS][_TARGET_TEMPERATURE]
        _zone_opmode = _zone[_SETPOINT_STATUS]['setpointMode']

        _cont_opmode = self.hass.data[DATA_EVOHOME]['status'] \
            ['systemModeStatus']['mode']

        _state = None



        if _cont_opmode == EVO_AWAY:    _state = EVO_AWAY      #(& target_temp = 10)
        if _cont_opmode == EVO_HEATOFF: _state = EVO_FROSTMODE #(& target_temp = 5)

# EVO_AUTOECO resets EVO_TEMPOVER (but not EVO_PERMOVER) to EVO_FOLLOW (EVO_AUTOECO)
# EVO_DAYOFF  resets EVO_TEMPOVER (but not EVO_PERMOVER) to EVO_FOLLOW (EVO_DAYOFF)



        if _zone_target == 55:
### TBA do I need to check if zone is in 'FollowSchedule' mode
            _LOGGER.info(
                "state(Zone=%s): Begin open window heuristics...",
                self._id
            )
            if _cont_opmode == EVO_HEATOFF:
                _state = EVO_FROSTMODE
            else:
#               if _zone_opmode == EVO_FOLLOW:
#                   if sched_temp = 5:
#                       _state = _zone_opmode
                _state = EVO_OPENWINDOW

                _LOGGER.info(
                    "state(Zone=%s): OpenWindow mode assumed",
                    self._id
                )

# if we haven't yet figured out the zone's state, then it must be one of these:
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



# c) Otherwise, the Zone's state is equal to as it's current operating mode
        _LOGGER.info(
            "state(Zone=%s) = %s [setpoint=%s, opmode=%s, cont_opmode=%s]",
            self._id + " [" + self._name + "]",
            _state,
            _zone_target,
            _zone_opmode,
            _cont_opmode,
        )
        return _state



    @property
    def device_state_attributes(self):
        """Return the optional state attributes."""

        _data = {}
        _data[ATTR_OPERATION_MODE] = self._getZoneById(self._id, 'status') \
            [_SETPOINT_STATUS]['setpointMode']

        _data[ATTR_OPERATION_LIST] = self._getZoneById(self._id, 'config') \
            [_SETPOINT_CAPABILITIES]['allowedSetpointModes']

        _LOGGER.info("device_state_attributes(Zone=%s) = %s", self._id + " [" + self._name + "]", _data)
        return _data


    @property
    def current_operation(self):
        """Return the current operation mode of the zone."""
        _opmode = self._getZoneById(self._id, 'status') \
            [_SETPOINT_STATUS]['setpointMode']

        _LOGGER.info("current_operation(Zone=%s) = %s", self._id + " [" + self._name + "]", _opmode)
        return _opmode


    @property
    def operation_list(self):
        """Return the list of available operation modes."""
        _oplist = self._getZoneById(self._id, 'config') \
            [_SETPOINT_CAPABILITIES]['allowedSetpointModes']

        _LOGGER.info("operation_list(Zone=%s) = %s", self._id + " [" + self._name + "]", _oplist)
        return _oplist


    def set_operation_mode(self, operation_mode, setpoint=None, until=None):
#   def set_operation_mode(self: ClimateDevice, operation: str, setpoint=None, until=None) -> None:
        """Set the operating mode for the zone."""
        _LOGGER.info(
            "set_operation_mode(Zone=%s, OpMode=%s, SetPoint=%s, Until=%s)",
            self._id + " [" + self._name + "]",
            operation_mode,
            setpoint,
            until
        )

#      _LOGGER.debug("for Zone=%s: set_operation_mode(operation_mode=%s, setpoint=%s, until=%s)", self._name, operation_mode, setpoint, until)

#       zone = self.client._get_single_heating_system().zones_by_id[self._id])
        zone = self.client.locations[0]._gateways[0]._control_systems[0].zones_by_id[self._id]

        _zone = self._getZoneById(self._id, 'status')
        _target_temperature = _zone[_SETPOINT_STATUS][_TARGET_TEMPERATURE]
#XX     _current_operation  = _zone[_SETPOINT_STATUS]['setpointMode']

        if operation_mode == EVO_FOLLOW:
            _LOGGER.debug("Calling client v2 API [? request(s)]: zone.cancel_temp_override()...",)
            zone.cancel_temp_override(zone)
            setpoint = self._getZoneSchedTemp(_zone['zoneId'], datetime.now())  ## Throws: KeyError: ("zone '", '3449703', "' not in dataSource")

        else:
            if setpoint is None:
                setpoint = _zone[_SETPOINT_STATUS][_TARGET_TEMPERATURE]

        if operation_mode == EVO_PERMOVER:
            _LOGGER.debug("Calling client v2 API [? request(s)]: zone.set_temperature(%s)...", setpoint)
            zone.set_temperature(setpoint)  ## override target temp indefinitely

# TBA this code is wrong ...
        if operation_mode == EVO_TEMPOVER:
            if until == None:
# UTC_OFFSET_TIMEDELTA = datetime.now() - datetime.utcnow()
                until = datetime.utcnow() + timedelta(1/24) ## use .utcnow() or .now() ??
            _LOGGER.debug("Calling client v2 API [? request(s)]: zone.set_temperature(%s, %s)...", setpoint, until)
            zone.set_temperature(setpoint, until)  ## override target temp (for a hour)

        _LOGGER.debug("Action completed, updating internal state data...")
        _zone[_SETPOINT_STATUS]['setpointMode'] = operation_mode
        _zone[_SETPOINT_STATUS][_TARGET_TEMPERATURE] = setpoint

        _LOGGER.debug(" - calling: controller.schedule_update_ha_state()")
        self.async_schedule_update_ha_state(force_refresh=False)

        return True


    @property
    def name(self):
        """Get the name of the zone."""
        _name = self._getZoneById(self._id, 'config')['name']
        _LOGGER.debug("name(Zone=%s) = %s", self._id, _name)
        return _name


    @property
    def icon(self):
        """Return the icon to use in the frontend UI."""
        _icon = "mdi:radiator"
        _LOGGER.debug("icon(Zone=%s) = %s", self._id , _icon)
        return _icon


    @property
    def supported_features(self):
        """Get the list of supported features of the zone."""
        _feats = SUPPORT_TARGET_TEMPERATURE | SUPPORT_OPERATION_MODE
        _LOGGER.info("supported_features(Zone=%s) = %s", self._id, _feats)
        return _feats


    def set_temperature(self, **kwargs):
        """Set a target temperature (setpoint) for the zone."""
        _LOGGER.info(
            "set_temperature(Zone=%s, **kwargs)",
            self._id + " [" + self._name + "]"
        )

#       for name, value in kwargs.items():
#          _LOGGER.debug('%s = %s', name, value)

        _temperature = kwargs.get(ATTR_TEMPERATURE)

        if _temperature is None:
#          _LOGGER.error("set_temperature(temperature=%s) is None!", _temperature)
            return False

        _zone = self._getZoneById(self._id, 'config')
        _max_temp = _zone[_SETPOINT_CAPABILITIES]['maxHeatSetpoint']

        if _temperature > _max_temp:
#          _LOGGER.error("set_temperature(temperature=%s) is above maximum!", _temperature)
            return False

        _min_temp = _zone[_SETPOINT_CAPABILITIES]['minHeatSetpoint']

        if _temperature < _min_temp:
#          _LOGGER.error("set_temperature(temperature=%s) is below minimum!", _temperature)
            return False

        _until = kwargs.get(ATTR_UNTIL)
#       _until = None  ## TBA
        _LOGGER.info("Calling API: zone.set_temperature(temp=%s, until=%s)...", _temperature, _until)

        zone = self.client._get_single_heating_system().zones[self._name]
#       zone = self.client.locations[0]._gateways[0]._control_systems[0].zones[self._name]
        zone.set_temperature(_temperature, _until)

# first update hass.data[DOMAIN]...
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


    @property
    def current_temperature(self):
        """Return the current temperature of the Zone."""
        _status = self._getZoneById(self._id, 'status')
        if _status['temperatureStatus']['isAvailable']:
            _temp = _status['temperatureStatus']['temperature']
            _LOGGER.info(
                "current_temperature(Zone=%s) = %s", 
                self._id + " [" + self._name + "]", 
                _temp
                )
        else:
            _temp = None
            _LOGGER.warn(
                "current_temperature(Zone=%s) - is unavailable", 
                self._id + " [" + self._name + "]"
                )
        return _temp


    @property
    def temperature_unit(self):
        """Get the unit of measurement of the Zone temperature/setpoint."""
        _LOGGER.debug("temperature_unit(Zone=%s) = %s", self._id, TEMP_CELSIUS)
        return TEMP_CELSIUS


    @property
    def precision(self):
        """Return the precision of the Zone temperature/setpoint."""
#       if not ?using v1 API? == TEMP_CELSIUS:
#           return PRECISION_HALVES
        _LOGGER.debug("precision(Zone=%s) = %s", self._id, PRECISION_TENTHS)
        return PRECISION_TENTHS


    @property
    def min_temp(self):
        """Return the minimum setpoint temperature.  Setpoints are 5-35C by
           default, but zones can be configured inside these values."""
        _temp = self._getZoneById(self._id, 'config') \
            [_SETPOINT_CAPABILITIES]['minHeatSetpoint']
        _LOGGER.debug("min_temp(Zone=%s) = %s", self._id, _temp)
        return _temp


    @property
    def max_temp(self):
        """Return the maximum setpoint temperature.  Setpoints are 5-35C by
           default, but zones can be configured inside these values."""
        _temp = self._getZoneById(self._id, 'config') \
            [_SETPOINT_CAPABILITIES]['maxHeatSetpoint']
        _LOGGER.debug("max_temp(Zone=%s) = %s", self._id, _temp)
        return _temp


    @property
    def scheduled_temperature(self, datetime=None):
        """Return the temperature we try to reach."""
        _temp = self._getZoneById(self._id, 'schedule')

        _LOGGER.debug("scheduled_temperature(Zone=%s) = %s", self._id, _temp)


    @property
    def target_temperature(self):
        """Return the temperature we try to reach."""

## get the system's current operation mode
        _opmode = self.hass.data[DATA_EVOHOME]['status'] \
            ['systemModeStatus']['mode']

        if _opmode == EVO_HEATOFF:
            _temp = 5
        elif _opmode == EVO_AWAY:
            _temp = 10
        else:
            _temp = self._getZoneById(self._id, 'status') \
                [_SETPOINT_STATUS][_TARGET_TEMPERATURE]

        _LOGGER.info(
            "target_temperature(Zone=%s) = %s", 
            self._id + " [" + self._name + "]", 
            _temp
            )
        return _temp


    @property
    def target_temperature_step(self):
        """Return the supported step of target temperature."""
        _step = self._getZoneById(self._id, 'config') \
            [_SETPOINT_CAPABILITIES]['valueResolution']  ## usu. PRECISION_HALVES
        _LOGGER.debug(
            "target_temperature_step(Zone=%s) = %s",
            self._id,
            _step
            )
        return _step



    def update(self):
        """Get the latest state (operating mode, temperature) of a zone."""
        _LOGGER.info("update(Zone=%s)", self._id + " [" + self._name + "]")

        ec_status = self.hass.data[DATA_EVOHOME]['status']
#       _LOGGER.debug("ec_status = %s.", ec_status)
        if ec_status == {}:
            _LOGGER.error("ec_status = %s.", ec_status)

        return



class evoDhwEntity(evoZoneEntity):
    """Honeywell evohome DHW Entity base."""

    def __init__(self, hass, client, dhw):
        """Initialize the evoEntity."""
###     super().__init__(hass, client, dhw)  ## do the following instead...
        self.hass = hass
        self.client = client
###
        self._id = dhw['dhwId']
        self._name = '_DHW'

        _LOGGER.info("__init__(dhw=%s)", self._id + " [" + self._name + "]")

        self._assumed_state = False

# listen for update packets...
        hass.helpers.dispatcher.async_dispatcher_connect(
            DISPATCHER_EVOHOME,
            self._connect
        )  # for: def async_dispatcher_connect(signal, target)

        return None


    @property
    def state(self):
        """Return the zone's current state (usually, its operation mode).

        A zone's state is usually its operation mode, but they may enter
        OpenWindowMode autonomously."""

        _zone = self.hass.data[DATA_EVOHOME]['status'] \
            ['dhw']

        _zone_state  = _zone['stateStatus']['state']
        _zone_opmode = _zone['stateStatus']['mode']

        _cont_opmode = self.hass.data[DATA_EVOHOME]['status'] \
            ['systemModeStatus']['mode']

        _state = _zone_state



#       if _cont_opmode == EVO_AWAY:    _state = EVO_AWAY      #(& target_temp = 10)
#       if _cont_opmode == EVO_HEATOFF: _state = EVO_FROSTMODE #(& target_temp = 5)

# EVO_AUTOECO resets EVO_TEMPOVER (but not EVO_PERMOVER) to EVO_FOLLOW (EVO_AUTOECO)
# EVO_DAYOFF  resets EVO_TEMPOVER (but not EVO_PERMOVER) to EVO_FOLLOW (EVO_DAYOFF)



# if we haven't yet figured out the zone's state, then it must be one of these:
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



# c) Otherwise, the Zone's state is equal to as it's current operating mode
        _LOGGER.info(
            "state(DHW=%s) = %s [setpoint=xx, opmode=%s, cont_opmode=%s]",
            self._id + " [" + self._name + "]",
            _state,
#           _zone_target,
            _zone_opmode,
            _cont_opmode,
        )
        return _state



    @property
    def device_state_attributes(self):
        return None

    @property
    def current_operation(self):
        return None

    @property
    def operation_list(self):
        return None

    def set_operation_mode(self, operation_mode, setpoint=None, until=None):
        return True

    @property
    def name(self):
        """Get the name of the DHW."""
        _LOGGER.debug("name(DHW=%s) = %s", self._id, self._name)
        return self._name

    @property
    def icon(self):
        """Return the icon to use in the frontend UI."""
        _icon = "mdi:thermometer-lines"
        _LOGGER.debug("icon(DHW=%s) = %s", self._id , _icon)
        return _icon

    def set_temperature(self, **kwargs):
        return True

    @property
    def current_temperature(self):
        """Return the current temperature of the DHW."""
        _status = self.hass.data[DATA_EVOHOME]['status']['dhw']
        if _status['temperatureStatus']['isAvailable']:
            _temp = _status['temperatureStatus']['temperature']
            _LOGGER.info(
                "current_temperature(DHW=%s) = %s", 
                self._id + " [" + self._name + "]", 
                _temp
                )
        else:
            _temp = None
            _LOGGER.warn(
                "current_temperature(DHW=%s) - is unavailable", 
                self._id + " [" + self._name + "]"
                )
        return _temp

    @property
    def temperature_unit(self):
        """Get the unit of measurement of the DHW temperature/setpoint."""
        _LOGGER.debug("temperature_unit(DHW=%s) = %s", self._id, TEMP_CELSIUS)
        return TEMP_CELSIUS


    @property
    def precision(self):
        """Return the precision of the DHW temperature/setpoint."""
        _LOGGER.debug("precision(Zone=%s) = %s", self._id, PRECISION_HALVES)
        return PRECISION_HALVES

    @property
    def min_temp(self):
        """Return the maximum setpoint temperature, ??-??C by default."""
        _temp = 40
        _LOGGER.debug("min_temp(DHW=%s) = %s", self._id, _temp)
        return _temp

    @property
    def max_temp(self):
        """Return the maximum setpoint temperature, ??-??C by default."""
        _temp = 70
        _LOGGER.debug("max_temp(DHW=%s) = %s", self._id, _temp)
        return _temp

    @property
    def scheduled_temperature(self, datetime=None):
        return 66

    @property
    def target_temperature(self):
        return 99

    @property
    def target_temperature_step(self):
        return PRECISION_HALVES


    def update(self):
        """Get the latest state (operating mode, temperature) of a zone."""
        _LOGGER.info("update(DHW=%s)", self._id + " [" + self._name + "]")

        ec_status = self.hass.data[DATA_EVOHOME]['status']
#       _LOGGER.debug("ec_status = %s.", ec_status)
        if ec_status == {}:
            _LOGGER.error("ec_status = %s.", ec_status)

        return


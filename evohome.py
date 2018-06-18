"""
Support for Honeywell (EU-only) Evohome installations: 1 controller & 1+ zones.

To install it, copy it to ${HASS_CONFIG_DIR}/custom_components.
The configuration.yaml as below...

evohome:
  username: !secret_evohome_username
  password: !secret_evohome_password
  scan_interval: 300
"""
# regarding: https://developers.home-assistant.io/docs/en/development_index.html
#  - checked with: flake8 --ignore=E303,E241 --max-line-length=150 evohome.py



import logging
import socket
from datetime import datetime, timedelta
from time import sleep, strftime, strptime, mktime
import sched
import functools as ft

import requests
import voluptuous as vol

from homeassistant.core import callback
from homeassistant.helpers.discovery import load_platform
from homeassistant.helpers.entity    import Entity

# from homeassistant.helpers.entity import Entity
from homeassistant.helpers.event import track_state_change

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

# CONF_HIGH_PRECISION='high_precision'

## https://www.home-assistant.io/developers/component_deps_and_reqs/
#  https://github.com/home-assistant/home-assistant.github.io/pull/5199

## these vars for >=0.2.6 (is it v3 of the api?)
#REQUIREMENTS = ['https://github.com/zxdavb/evohome-client/archive/dev.zip#evohomeclient==0.2.16']
#_SETPOINT_CAPABILITIES = 'setpointCapabilities'
#_SETPOINT_STATUS       = 'setpointStatus'
#_TARGET_TEMPERATURE    = 'targetHeatTemperature'
#_OAUTH_TIMEOUT_SECONDS = 1800 - 120  ## timeout is 30 mins

## these vars for <=0.2.5...
REQUIREMENTS = ['evohomeclient==0.2.5']
_SETPOINT_CAPABILITIES = 'heatSetpointCapabilities'
_SETPOINT_STATUS       = 'heatSetpointStatus'
_TARGET_TEMPERATURE    = 'targetTemperature'
_OAUTH_TIMEOUT_SECONDS = 3600 - 120  ## timeout is 60 mins

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
        vol.Optional(CONF_SCAN_INTERVAL, default=300): cv.positive_int,
#       vol.Optional('high_precision', default=False): cv.boolean,
    }),
}, extra=vol.ALLOW_EXTRA)


## Operations: Usually, a Mode causes a corresponding State, except...
# 'AutoWithReset'  is only an Operation Mode:  it leads to 'Auto'
# 'OpenWindowMode' is only an Operating State: Zone remains in its own mode

# these are for controllers
EVO_RESET      = 'AutoWithReset'
EVO_AUTO       = 'Auto'
EVO_AUTOECO    = 'AutoWithEco'
EVO_AWAY       = 'Away'
EVO_DAYOFF     = 'DayOff'
EVO_CUSTOM     = 'Custom'
EVO_HEATOFF    = 'HeatingOff'
# these are for zones
EVO_FOLLOW     = 'FollowSchedule'
EVO_PERMOVER   = 'TemporaryOverride'
EVO_TEMPOVER   = 'PermanentOverride'
EVO_OPENWINDOW = 'OpenWindow'


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

## Load platforms...
    load_platform(hass, 'climate', DOMAIN)

    _LOGGER.info("Finished: setup()")
    return True


def _updateStateData(evo_client, domain_data, force_refresh = False):

    if force_refresh is True:
        domain_data['evohomeClient'] = evo_client

# OAuth tokens need periodic refresh, but the client exposes no api for that
        timeout = datetime.now() + timedelta(seconds = _OAUTH_TIMEOUT_SECONDS)

        domain_data['tokenExpires'] = timeout
            
        _LOGGER.info("OAuth token expires at %s", timeout)

# These are usually updated once per authentication cycle...
        domain_data['installation'] \
            = _returnConfiguration(evo_client)
        domain_data['schedule'] \
            = _returnZoneSchedules(evo_client)
#       domain_data['lastRefreshed'] \
#           = datetime.now()

# These are usually updated once per 'scan_interval' cycle...
    domain_data['status'] \
        = _returnTempsAndModes(evo_client)
    domain_data['lastUpdated'] \
        = datetime.now()

# Some of this data should be redacted before getting into the logs
    if _LOGGER.isEnabledFor(logging.INFO) and force_refresh is True:
        _temp = domain_data
        _temp['installation']['locationInfo']['postcode'] = 'REDACTED'
        _temp['schedule'] = {}

        _LOGGER.info("hass.data[DATA_EVOHOME]: %s", _temp)
        _temp = ""

    return True


def _returnConfiguration(client, force_update = False):
## client.installation_info[0] is more efficient than client.fullInstallation()
    _LOGGER.info("_returnConfiguration(client)")

    if force_update is True: # BUG: or client.installation_info = Null
        _LOGGER.info("Calling client v2 API [? request(s)]: client.installation(FORCE)...")
        client.installation()           # this will cause a new call, and...

    _LOGGER.info("Calling client v2 API [0 request(s)]: client.installation_info[0]...")
    _tmp = client.installation_info[0] # this attribute is updated by that call

# Now redact unneeded info
    _tmp['locationInfo']['locationId'] = 'REDACTED'
    _tmp['locationInfo']['streetAddress'] = 'REDACTED'
    _tmp['locationInfo']['city'] = 'REDACTED'

    _tmp['locationInfo']['locationOwner']['userId'] = 'REDACTED'
    _tmp['locationInfo']['locationOwner']['username'] = 'REDACTED'
    _tmp['locationInfo']['locationOwner']['firstname'] = 'REDACTED'
    _tmp['locationInfo']['locationOwner']['lastname'] = 'REDACTED'

    _tmp['gateways'][0]['gatewayInfo']['gatewayId'] = 'REDACTED'
    _tmp['gateways'][0]['gatewayInfo']['mac'] = 'REDACTED'
    _tmp['gateways'][0]['gatewayInfo']['crc'] = 'REDACTED'

    return _tmp


def _returnTempsAndModes(client, force_update = False):
## Get the latest modes/temps (assumes only 1 location/controller)
    _LOGGER.info("_returnTempsAndModes(client)")

#   if force_update is True:
#        _LOGGER.info("Calling client v2 API [?x]: client.installation()...")
#       hass.data[DATA_EVOHOME]['installation'] = client.installation()

    _LOGGER.info("Calling client v2 API [1 request(s)]: client.locations[0].status()...")
    ec2_status = client.locations[0].status()  # get latest modes/temps
    ec2_tcs = ec2_status['gateways'][0]['temperatureControlSystems'][0]

    _LOGGER.debug("ec2_api.status() = %s", ec2_status)

    if True:
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
            _LOGGER.error("Failed to utilize the Honeywell web v1 client (for higher precision temps)")
            raise

        finally:
#           ec1_api = None  # do I need to clean this up?
            pass


    if _LOGGER.isEnabledFor(logging.DEBUG):
        for zone in ec2_tcs['zones']:
            _LOGGER.debug("update(controller) - for child %s (%s), temp = %s.", zone['zoneId'], zone['name'], zone['temperatureStatus']['temperature'])

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

#   _LOGGER.debug("ec2_api.status() = %s", ec2_status)
    return schedules  # client.zone_schedules_backup()



class evoEntity(Entity):
    """Honeywell evohome Entity base."""

    def __init__(self, hass, client, device=None):
        """Initialize the evoEntity."""
        self.hass = hass
        self.client = client

        self._operation_list = []
        self._current_operation = None

        return True



class evoControllerEntity(evoEntity):
    """Honeywell evohome Controller Entity base."""

    def __init__(self, hass, client, controller):
        """Initialize the evoEntity."""
        super().__init__(hass, client, controller)

        self._id = controller['systemId']
#       self._name = "_" + controller['modelType']  # named so is first in list

        _LOGGER.info("__init__(Controller=%s)", self._id)

# Update immediately after entity has initialized -how?
        return True


        
    @property
    def name(self):
        """Get the name of the controller."""
        _LOGGER.info("name(Controller=%s)", self._id)
        return "_" + self.hass.data[DATA_EVOHOME]['installation'] \
            ['locationInfo']['name']


    @property
    def icon(self):
        """Return the icon to use in the frontend UI."""
        _LOGGER.info("icon(Controller=%s)", self._id)
        return "mdi:thermostat"


    @property
    def state(self):
        """Return the controller's current state (usually, its operation mode).

        After calling AutoWithReset, the controller  will enter Auto mode."""

        _LOGGER.info("state(Controller=%s)", self._id)

        _controller_opmode = self.hass.data[DATA_EVOHOME]['status'] \
            ['systemModeStatus']['mode']
        
        if _controller_opmode == EVO_RESET:
            _LOGGER.debug("state(Controller=%s): changed from %s to %s.", self._id, EVO_RESET, EVO_AUTO)
            return EVO_AUTO
        else:
            _LOGGER.debug("state(Controller=%s): unchanged as %s.", self._id, self._current_operation)
            return _controller_opmode


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
        _LOGGER.info("operation_list(Controller=%s)", self._id)

        _oplist = []
        for mode in self.hass.data[DATA_EVOHOME]['installation'] \
            ['gateways'][0]['temperatureControlSystems'][0]['allowedSystemModes']:
            _oplist.append(mode['systemMode'])

        _LOGGER.info("operation_list(Controller=%s) = %s", self._id, _oplist)
        return _oplist

        
    def async_set_operation_mode(self, operation_mode):
        """Set new target operation mode.

        This method must be run in the event loop and returns a coroutine.
        """
        return self.hass.async_add_job(self.set_operation_mode, operation_mode)

        
    def set_operation_mode(self, operation_mode):
#   def set_operation_mode(self: ClimateDevice, operation: str) -> None:
        """Set new target operation mode.

        'AutoWithReset may not be a mode in itself: instead, it _should_(?) lead
        to 'Auto' mode after resetting all the zones to 'FollowSchedule'. How
        should this be done?

        'Away' mode applies to the controller, not it's (slave) zones.

        'HeatingOff' doesn't turn off heating, instead: it simply sets setpoints
        to a minimum value (i.e. FrostProtect mode)."""

        _LOGGER.info(
            "set_operation_mode(Controller=%s, operation_mode=%s)",
            self._id,
            operation_mode
        )

### Controller: operations vs (operating) modes...
        _opmode = self.hass.data[DATA_EVOHOME]['status'] \
            ['systemModeStatus']['mode']


        if operation_mode == _opmode:
            _LOGGER.info(" - operation mode unchanged??")

# "AutoWithReset", after resetting all the zones to "FollowSchedule", _should_ lead to "Auto" mode (but doesn't?)
        if operation_mode == EVO_RESET:  ## a private function in the client API (it is not exposed)
        ## here, we call
            _LOGGER.info(
                "Calling client API: controller.set_status_%s())...",
                operation_mode
            )
            _AUTOWITHRESET = 5
            _LOGGER.info("Calling client v2 API [?x]: controller._set_status()...")
#           self.client._get_single_heating_system._set_status(_AUTOWITHRESET)
            self.client.locations[0]._gateways[0]._control_systems[0]._set_status(_AUTOWITHRESET)

        else:
            self._current_operation = operation_mode
# There is no EvohomeClient.set_status_reset exposed via the client v2 API (<=2.6), so
# we're using EvohomeClient...ControlSystem._set_status(5) instead.
            functions = {
#               EVO_AUTORESET: self.client.locations[0]._gateways[0]._control_systems[0]._set_status(5),
                EVO_AUTO:    self.client.set_status_normal,
                EVO_AUTOECO: self.client.set_status_eco,
                EVO_DAYOFF:  self.client.set_status_dayoff,
                EVO_AWAY:    self.client.set_status_away,
                EVO_HEATOFF: self.client.set_status_heatingoff,
                EVO_CUSTOM:  self.client.set_status_custom,
            }

# before calling func(), should check OAuth token still viable, but how?
            func = functions[operation_mode]
            _LOGGER.info(
                "Calling client API: controller.set_status_%s())...",
                operation_mode
            )
            func()


            if operation_mode == EVO_RESET:
                _LOGGER.info("ZZ Attempting to update Zone state data (%s), EVO_RESET")
                _zones = self.hass.data[DATA_EVOHOME]['status']['zones']
                for _zone in _zones:
                    _zone[_SETPOINT_STATUS]['setpointMode'] == EVO_FOLLOW
                    _zone[_SETPOINT_STATUS][_TARGET_TEMPERATURE] \
                        = self._getZoneById(self._id, 'schedule')['name']


            if operation_mode == EVO_AUTOECO:
                _LOGGER.info("ZZ Attempting to update Zone state data (%s), EVO_AUTOECO")
                _zones = self.hass.data[DATA_EVOHOME]['status']['zones']
                for _zone in _zones:
                    if _zone[_SETPOINT_STATUS]['setpointMode'] == EVO_FOLLOW:
                        _zone[_SETPOINT_STATUS][_TARGET_TEMPERATURE] \
                            = _zone[_SETPOINT_STATUS][_TARGET_TEMPERATURE] - 3


            if operation_mode == EVO_HEATOFF:
                _LOGGER.info("ZZ Attempting to update Zone state data (%s), EVO_HEATOFF")
                _zones = self.hass.data[DATA_EVOHOME]['status']['zones']
                for _zone in _zones:
                    _zone[_SETPOINT_STATUS][_TARGET_TEMPERATURE] = 5


        return


    @property
    def state_attributes(self):
        """Return the optional state attributes."""
        _LOGGER.info("state_attributes(Controller=%s)",  self._id)

        _data = {}

#       if self.supported_features & SUPPORT_OPERATION_MODE:
        if True and SUPPORT_OPERATION_MODE:
#           _data[ATTR_OPERATION_MODE] = self.current_operation
            _data[ATTR_OPERATION_MODE] = self.hass.data[DATA_EVOHOME] \
                ['status']['systemModeStatus']['mode']
            
#           _data[ATTR_OPERATION_LIST] = self.operation_list
            _oplist = []
            for mode in self.hass.data[DATA_EVOHOME]['installation'] \
                ['gateways'][0]['temperatureControlSystems'][0]['allowedSystemModes']:
                _oplist.append(mode['systemMode'])
            _data[ATTR_OPERATION_LIST] = _oplist

        _LOGGER.info("state_attributes(Controller) = %s", _data)
        return _data


    @property
    def device_state_attributes(self):
        """Return the optional state attributes."""
        _LOGGER.info("device_state_attributes(Controller=%s)", self._id)

        _data = {}

        _LOGGER.info("device_state_attributes(Controller) = %s", _data)
        return _data


    @property
    def supported_features(self):
        """Get the list of supported features of the controller."""
## It will likely be the case we need to support Away/Eco/Off modes in the HA fashion
## even though these modes are subtly different - this will allow tight integration
## with the HA landscape / other HA components, e.g. Alexa/Google integration
        _LOGGER.info("supported_features(Controller=%s)", self._id)
        return SUPPORT_OPERATION_MODE


    def update(self):
        """Get the latest state (operating mode) of the controller and
        update the state (temp, setpoint) of all children zones.

        Get the latest schedule of the controller every hour."""
#       _LOGGER.info("update(Controller=%s)", self._id)

## wait a minimum of 2 mins between updates
        _lastUpdated = self.hass.data[DATA_EVOHOME]['lastUpdated']

        if datetime.now() < _lastUpdated + timedelta(seconds = 115):
            return

        _LOGGER.info("update(Controller=%s)", self._id)

## TBA: no provision (yet) for DHW

## If the OAuth token has expired, we need to re-authenticate to get another
        timeout = self.hass.data[DATA_EVOHOME]['tokenExpires']

# Do we perform only an update, or a full refresh (incl. OAuth access token)?
        if datetime.now() > timeout:
            _LOGGER.info("Re-Authenticating as OAuth token expired %s", timeout)
            try:  ## client._login() 
                _LOGGER.info("Calling client v2 API [4 request(s)]: client._login()...")
                self.client._login()
            except:
                _LOGGER.error("Failed to re-connect to the Honeywell web API!")
                raise

        _updateStateData(self.client, self.hass.data[DATA_EVOHOME])

        return True



class evoZoneEntity(evoEntity, ClimateDevice):
    """Honeywell evohome Zone Entity base."""

    def __init__(self, hass, client, zone):
        """Initialize the evoEntity."""
        super().__init__(hass, client, zone)

        self._id = zone['zoneId']
        self._name = zone['name']  ## TBA - remove this??

        _LOGGER.info("__init__(zone=%s)", self._id + " [" + self._name + "]")

# ???       
        return True


    def _getZoneById(self, zoneId, dataSource='status'):

        if dataSource == 'schedule':
            _zones = self.hass.data[DATA_EVOHOME]['schedule']

            if zoneId in _zone:
                return _zone[zoneId]
            else:
                raise KeyError("zone '%s' not in dataSource", zoneId)
            
        if dataSource == 'config':
            _zones = self.hass.data[DATA_EVOHOME]['installation'] \
                ['gateways'][0]['temperatureControlSystems'][0]['zones']
        if dataSource == 'status':
            _zones = self.hass.data[DATA_EVOHOME]['status']['zones']

        for _zone in _zones:
            if _zone['zoneId'] == zoneId:
                return _zone
# or should this be an IndexError?               
        raise KeyError("Zone ID '%s' not found in dataSource", zoneId)


    def _getZoneSchedTemp(self, zoneId, timeOfDay=None, dayOfWeek=None):
        self._getZoneById(zoneId, 'schedule')['schedule']
# TBA      
        return _setPoint


    @property
    def name(self):
        """Get the name of the zone."""
        _name = self._getZoneById(self._id, 'config')['name']
        _LOGGER.info("name(Zone=%s) = %s", self._id + " [" + self._name + "]", _name)
        return _name


    @property
    def icon(self):
        """Return the icon to use in the frontend UI."""
        _icon = "mdi:radiator"
        _LOGGER.info("icon(Zone=%s) = %s", self._id + " [" + self._name + "]", _icon)
        return _icon


    @property
    def state(self):
        """Return the zone's current state (usually, its operation mode).

        A zone's state is usually its operation mode, but they may enter
        OpenWindowMode autonomously."""
        _LOGGER.info("state(Zone=%s)", self._id + " [" + self._name + "]")

        _controller_opmode = self.hass.data[DATA_EVOHOME]['status'] \
            ['systemModeStatus']['mode']
        
        _zone = self._getZoneById(self._id, 'status')
        _target_temperature = _zone[_SETPOINT_STATUS][_TARGET_TEMPERATURE]
        _current_operation  = _zone[_SETPOINT_STATUS]['setpointMode']
            
        _LOGGER.info(
            "state(Zone=%s): Controller is in '%s' mode",
            self._id,
            _controller_opmode
        )

### TBA do I need to check if zone is in 'FollowSchedule' mode
        if _target_temperature == 5:
            _LOGGER.info(
                "state(Zone=%s): Begin open window heuristics...",
                self._id
            )


# a) If Controller set to 'Mode', then Zone's state is 'State'
            if _controller_opmode == EVO_HEATOFF \
                and _current_operation == EVO_FOLLOW:
                _LOGGER.info("state(Zone=%s) = %s", self._id, "FrostProtect")
                return "FrostProtect"

# a) If Controller set to 'Auto' (or 'AutoWithReset'), then Zone's state is '???'
# a) If Controller set to 'AutoWithEco', then Zone's state is '???'
# a) If Controller set to 'Away', then Zone's state is '???'
# a) If Controller set to 'DayOff', then Zone's state is '???'

# b) Or, if the target_temp is 5, maybe an open window was detected?
#           if sched-temp <> 5:
            if _current_operation == EVO_FOLLOW:
                _LOGGER.info(
                    "state(Zone=%s): OpenWindow mode assumed",
                    self._id
                )
                _LOGGER.info("state(Zone=%s) = %s", self._id, EVO_OPENWINDOW)
                return EVO_OPENWINDOW

# c) Otherwise, the Zone's state is equal to as it's current operating mode
        if True:
            _LOGGER.debug(
                "state(Zone=%s): unchanged as %s.",
                self._id,
                _current_operation
            )

            _LOGGER.info("state(Zone=%s) = %s", self._id + " [" + self._name + "]", _current_operation)
            return _current_operation



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

#       zone = self.client._get_single_heating_system.zones_by_id[self._id])
        zone = self.client.locations[0]._gateways[0]._control_systems[0].zones_by_id[self._id]

        if operation_mode == 'FollowSchedule':
            _LOGGER.debug("Calling API: zone.cancel_temp_override()")
            zone.cancel_temp_override(zone)

        else:
            if setpoint == None:
                setpoint = self._target_temperature

            if operation_mode == 'PermanentOverride':
                _LOGGER.debug("Calling API: zone.set_temperature(%s)...", setpoint)
                zone.set_temperature(setpoint)  ## override target temp indefinitely

            else:
                if until == None:
#                   UTC_OFFSET_TIMEDELTA = datetime.now() - datetime.utcnow()
                    until = datetime.utcnow() + timedelta(1/24) ## use .utcnow() or .now() ??

                if operation_mode == 'TemporaryOverride':
                    _LOGGER.debug("Calling API: zone.set_temperature(%s, %s)...", setpoint, until)
                    zone.set_temperature(setpoint, until)  ## override target temp (for a hour)


        self._operating_mode = operation_mode
        self._target_temperature = setpoint

        _LOGGER.debug("Updating state data")
        for z in self.hass.data[DATA_EVOHOME]['status']['zones']:
            if z['zoneId'] == self._id:
                z[_SETPOINT_STATUS]['setpointMode'] = self._operating_mode
                z[_SETPOINT_STATUS][_TARGET_TEMPERATURE] = self._target_temperature

#       _LOGGER.debug("refreshEverything(): controller.schedule_update_ha_state()")
#       self.schedule_update_ha_state()



    @property
    def supported_features(self):
        """Get the list of supported features of the zone."""
        _feats = SUPPORT_TARGET_TEMPERATURE | SUPPORT_OPERATION_MODE
        _LOGGER.info("supported_features(Zone=%s) = %s", self._id, _feats)
        return _feats


    @property
    def precision(self):
        """Return the precision of the system."""
#       if not ?using v1 API? == TEMP_CELSIUS:
#           return PRECISION_HALVES
        _LOGGER.info("precision(Zone=%s) = %s", self._id, PRECISION_TENTHS)
        return PRECISION_TENTHS


    @property
    def temperature_unit(self):
        """Get the unit of measurement of the controller."""
        _LOGGER.info("temperature_unit(Zone=%s) = %s", self._id, TEMP_CELSIUS)
        return TEMP_CELSIUS


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
        _LOGGER.info("ZX Calling API: zone.set_temperature(temp=%s, until=%s)...", _temperature, _until)

#       zone = self.client._get_single_heating_system.zones[self._name]
        zone = self.client.locations[0]._gateways[0]._control_systems[0].zones[self._name]
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
        """Return the current temperature."""
        _temp = self._getZoneById(self._id, 'status') \
            ['temperatureStatus']['temperature']
        _LOGGER.info("current_temperature(Zone=%s) = %s", self._id + " [" + self._name + "]", _temp)
        return _temp


    @property
    def target_temperature(self):
        """Return the temperature we try to reach."""
        _temp = self._getZoneById(self._id, 'status') \
            [_SETPOINT_STATUS][_TARGET_TEMPERATURE]
        _LOGGER.info("target_temperature(Zone=%s) = %s", self._id + " [" + self._name + "]", _temp)
        return _temp


    @property
    def target_temperature_step(self):
        """Return the supported step of target temperature."""
        _LOGGER.info("target_temperature_step(Zone=%s)", self._id)
#       return PRECISION_HALVES
        return self._getZoneById(self._id, 'config') \
            [_SETPOINT_CAPABILITIES]['valueResolution']


    @property
    def min_temp(self):
        """Return the minimum setpoint temperature.  Setpoints are 5-35C by
           default, but zones can be configured inside these values."""
        _temp = self._getZoneById(self._id, 'config') \
            [_SETPOINT_CAPABILITIES]['minHeatSetpoint']
        _LOGGER.info("min_temp(Zone=%s) = %s", self._id, _temp)
        return _temp


    @property
    def max_temp(self):
        """Return the maximum setpoint temperature.  Setpoints are 5-35C by
           default, but zones can be configured inside these values."""
        _temp = self._getZoneById(self._id, 'config') \
            [_SETPOINT_CAPABILITIES]['maxHeatSetpoint']
        _LOGGER.info("max_temp(Zone=%s) = %s", self._id, _temp)
        return _temp



    def update(self):
        """Get the latest state (operating mode, temperature) of a zone."""
        _LOGGER.info("update(Zone=%s)", self._id + " [" + self._name + "]")

        ec_status = self.hass.data[DATA_EVOHOME]['status']
#       _LOGGER.debug("ec_status = %s.", ec_status)
        if ec_status == {}:
            _LOGGER.error("ec_status = %s.", ec_status)

        return





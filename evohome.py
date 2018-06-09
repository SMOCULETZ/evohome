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
REQUIREMENTS = ['evohomeclient==0.2.5']

## https://www.home-assistant.io/components/logger/
_LOGGER = logging.getLogger(__name__)

DOMAIN = 'evohome'

CONFIG_SCHEMA = vol.Schema({
    DOMAIN: vol.Schema({
    vol.Required(CONF_USERNAME): cv.string,
    vol.Required(CONF_PASSWORD): cv.string,
    vol.Optional(CONF_SCAN_INTERVAL, default=300): cv.positive_int,
    vol.Optional('high_precision', default=False): cv.boolean,
    })}, extra=vol.ALLOW_EXTRA)


## how long the OAuth token last for in evohome-client
_OAUTH_TIMEOUT_SECONDS = 3480  ## is actually 3600s, or 1hr
_OAUTH_TIMEOUT_FORMAT = '%Y-%m-%d %H:%M:%S'
DATA_EVOHOME = 'data_evohome'
DISPATCHER_EVOHOME = 'dispatcher_evohome'
DOMAIN='evohome'


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
EVO_OPENWINDOW = 'OpenWindowMode'



def setup(hass, config):
    """Set up a Honeywell evoTouch heating system (1 controller and multiple zones)."""

## BUG in HA? https://github.com/home-assistant/home-assistant/issues/11750 - doesn't affect most people
# - UnicodeEncodeError: 'ascii' codec can't encode character '\xb0' in position xxx: ordinal not in range(128)
# - see: https://askubuntu.com/questions/162391/how-do-i-fix-my-locale-issue
# - try: iconv -t utf-8 ~/.homeassistant/configuration.yaml
    _LOGGER.info("Started: setup(), temp units: %s...", TEMP_CELSIUS)

### Need to add code to exclude US-based systems...
    username = config[DOMAIN][CONF_USERNAME]  # config.get(CONF_USERNAME) doesn't work
    password = config[DOMAIN][CONF_PASSWORD]

# Use the evohome-client v2 API (which uses OAuth)
    from evohomeclient2 import EvohomeClient as EvohomeClient

    _LOGGER.info("Connecting to the Honeywell web API now...")
    try:
## Open a session to Honeywell's servers - this call includes:
# - ec_api._login():       ec_api.access_token (ec_api.username & .password)
# - ec_api.user_account(): ec_api.account_info
# - ec_api.installation(): ec_api.installation_info[0] (ec_api.system_id)

        ec_api = EvohomeClient(username, password, debug=True)

    except:
        _LOGGER.error("Failed to connect to the Honeywell web API.")
        raise

# the OAuth token needs refreshing after 1hr, but evohome-client provides no means of doing so...
    lastupdate = datetime.now()
    timeout = lastupdate + timedelta(seconds = _OAUTH_TIMEOUT_SECONDS)

    lastupdate = lastupdate.strftime(_OAUTH_TIMEOUT_FORMAT)
    timeout = timeout.strftime(_OAUTH_TIMEOUT_FORMAT)

    _LOGGER.info(" - connected OK, OAuth token expires at %s", timeout)

    hass.data[DATA_EVOHOME] = {}

# Link to the domain's client api...
    hass.data[DATA_EVOHOME]['evohomeClient'] = ec_api
    hass.data[DATA_EVOHOME]['tokenExpires']  = timeout

## TODO: Collect Controller / Zones as object refs, rather than their IDs
#   - ec_api._get_single_heating_system
#   - ec_api._get_single_heating_system.zones
#   - ec_api.locations[0]._gateways[0]._control_systems[0]
#   - ec_api.locations[0]._gateways[0]._control_systems[0].zones

# Fully update the domain's state data (only 1 location/controller for now)...
    hass.data[DATA_EVOHOME]['installation'] = _returnConfiguration(ec_api)
#   hass.data[DATA_EVOHOME]['schedule']     = _returnZoneSchedules(ec_api)
    hass.data[DATA_EVOHOME]['status']       = _returnTempsAndModes(ec_api)
    hass.data[DATA_EVOHOME]['lastUpdated']  = lastupdate

    _LOGGER.info("hass.data[DATA_EVOHOME]: %s", hass.data[DATA_EVOHOME])

## Load platforms...
    load_platform(hass, 'climate', DOMAIN)

    _LOGGER.info("Finished: setup()")
    return True




def _returnConfiguration(client, force_update = False):
## client.installation_info[0] is more efficient than client.fullInstallation()
    _LOGGER.info("_returnConfiguration(client)")
    if force_update is True:
        _LOGGER.info("Calling client API: client.installation()...")
        client.installation()           # this will cause a new call, and...

    return client.installation_info[0]  # this attribute is updated by that call


def _returnTempsAndModes(client, force_update = False):
## Get the latest modes/temps (assume only 1 location/controller)
    _LOGGER.info("_returnTempsAndModes(client)")

    if force_update is True:
        hass.data[DATA_EVOHOME]['installation'] = client.installation()

    _LOGGER.info("Calling client API: client.locations[0].status()...")
    ec2_status = client.locations[0].status()  # get latest modes/temps
    ec2_tcs = ec2_status['gateways'][0]['temperatureControlSystems'][0]

    _LOGGER.debug("ec2_api.status() = %s", ec2_status)

    try:
        _LOGGER.info('Connecting to the Honeywell web v1 API for higher precision temps...')

        from evohomeclient import EvohomeClient as EvohomeClientVer1  ## uses v1 of the api
        ec1_api = EvohomeClientVer1(client.username, client.password)
        _LOGGER.info(" - connected OK: ec1_api")

    except:
        _LOGGER.error("Failed to connect to the Honeywell web v1 API (for higher precision temps).")
        raise

    _LOGGER.info("Calling client v1 API: client.temperatures(force_refresh = True)...")
    ec1_temps = ec1_api.temperatures(force_refresh = True)  # is a generator?
    _LOGGER.debug("ev_api.temperatures() = %s", ec1_temps)

    for temp in ec1_temps:
        _LOGGER.debug("Zone %s (%s) reports temp %s", temp['id'], temp['name'], temp['temp'])

        for zone in ec2_tcs['zones']:
            _LOGGER.debug(" - is it slave %s (%s)?", zone['zoneId'], zone['name'])

            if int(temp['id']) == int(zone['zoneId']):
                _LOGGER.debug(" - matched: temp changed from %s to %s.", zone['temperatureStatus']['temperature'], temp['temp'])
                _LOGGER.debug(" - matched: temp for child %s (%s) changed from %s to %s.", zone['zoneId'], zone['name'], zone['temperatureStatus']['temperature'], temp['temp'])
                zone['temperatureStatus']['temperature'] = temp['temp']

                break

        ec1_api = None  # do I need to clean this up?

    if _LOGGER.isEnabledFor(logging.DEBUG):
        for zone in ec2_tcs['zones']:
            _LOGGER.debug("update(controller) - for child %s (%s), temp = %s.", zone['zoneId'], zone['name'], zone['temperatureStatus']['temperature'])

    return ec2_tcs


def _returnZoneSchedules(client):
# the client api does not expose a way to do this (it outputs to a file)
    _LOGGER.info("_returnZoneSchedules(client)")

    schedules = {}

## Collect each (slave) zone as a (climate component) device
    _LOGGER.info("Retreiving schedule for all zones")
    for z in client._get_single_heating_system()._zones:
        _LOGGER.info("Calling client API: client.zone.schedule(%s)...", z.zoneId)
        s = z.schedule()
        schedules[z.zoneId] = {'name': z.name, 'schedule': s}

#   _LOGGER.debug("ec2_api.status() = %s", ec2_status)
    return schedules  # client.zone_schedules_backup()


def refreshEverything():
    _LOGGER.info("refreshEverything(): controller.schedule_update_ha_state()")
    self.schedule_update_ha_state()

    _LOGGER.info("refreshEverything(): About to send UPDATE packet...")
    self.hass.helpers.dispatcher.async_dispatcher_send(DISPATCHER_EVOHOME, "UPDATE")        ## def async_dispatcher_send(hass, signal, *args):

    return NotImplementedError





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
        self._name = "_" + controller['modelType']  # named so is first in list

        _LOGGER.info("__init__(controller=%s)",
            self._name + " [" + self._name + "]",
            )

#       self._operation_list = []  # done in super()
        for mode in controller['allowedSystemModes']:
            self._operation_list.append(mode['systemMode'])
        _LOGGER.info("__init__(controller): operation_list = %s)", self._operation_list)

        self._current_operation = hass.data[DATA_EVOHOME]['status']['systemModeStatus']['mode']
        _LOGGER.info("__init__(controller): current_operation = %s)", self._current_operation)

# Update immediately after entity has initialized -how?

        return True


    @property
    def should_poll(self):
        """The controller will provide the state data."""
        _LOGGER.info("should_poll(ControllerEntity=%s): %s", self._id, True)
        return True


    @property
    def force_update(self):
#   def poll(self):
        """Controllers should update when state date is updated, even if it is unchanged."""
        return True


    @property
    def name(self):
        """Get the name of the controller."""
        _LOGGER.info("name(ControllerEntity=%s): %s", self._id, self._name)
        return self._name


    @property
    def icon(self):
        """Return the icon to use in the frontend UI."""
        _LOGGER.info("icon(ControllerEntity=%s): %s", self._id, "mdi:thermostat")
        return "mdi:thermostat"


    @property
#   def state(self) -> str:
    def state(self):
        """Return the controller's current state (usually, its operation mode).

        After calling AutoWithReset, the controller  will enter Auto mode."""
        _LOGGER.debug("state(ControllerEntity=%s): %s...", self._id, self._current_operation)

        if self._current_operation == EVO_RESET:
            _LOGGER.debug("state(ControllerEntity=%s): changed from %s to %s.", self._id, EVO_RESET, EVO_AUTO)
            return EVO_AUTO
        else:
            _LOGGER.debug("state(ControllerEntity=%s): unchanged as %s.", self._id, self._current_operation)
            return self._current_operation


    @property
    def current_operation(self):
        """Return the operation mode of the controller."""
        _LOGGER.info("current_operation(ControllerEntity=%s): %s", self._id, self._current_operation)
#       return hass.data[DATA_EVOHOME]['status']['systemModeStatus']['mode']
#       return getattr(self.client, ATTR_SYSTEM_MODE, None)
        return self._current_operation


    @property
    def operation_list(self):
        """Return the list of available operation modes."""
        _LOGGER.info("operation_list(ControllerEntity=%s): %s", self._id, self._operation_list)
        return self._operation_list


#   def set_operation_mode(self: ClimateDevice, operation: str) -> None:
    def set_operation_mode(self, operation_mode):
        """Set new target operation mode.

        'AutoWithReset may not be a mode in itself: instead, it _should_(?) lead
        to 'Auto' mode after resetting all the zones to 'FollowSchedule'. How
        should this be done?

        'Away' mode applies to the controller, not it's (slave) zones.

        'HeatingOff' doesn't turn off heating, instead: it simply sets setpoints
        to a minimum value."""

        _LOGGER.info("set_operation_mode(ControllerEntity=%s, mode=%s).", self._id, operation_mode)

### Controller: operations vs (operating) modes...

        if operation_mode == self._current_operation:
            _LOGGER.info(" - operation mode unchanged??")

# "AutoWithReset", after resetting all the zones to "FollowSchedule", _should_ lead to "Auto" mode (but doesn't?)
        if operation_mode == EVO_RESET:  ## a private function in the client API (it is not exposed)
        ## here, we call
            _LOGGER.info("Calling client API: controller.set_status_%s())...", operation_mode)
            _AUTOWITHRESET = 5
#           self.client._get_single_heating_system._set_status(_AUTOWITHRESET)
            self.client.locations[0]._gateways[0]._control_systems[0]._set_status(_AUTOWITHRESET)
#           self._current_operation = "Auto"  ## this doesn't work

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
            _LOGGER.info("Calling client API: controller.set_status_%s())...", operation_mode)
            func()

#       sleep(10)  # allow system to quiesce...


## Finally, send a message informing the kids that operting mode has changed?...
#       self.hass.bus.fire('mode_changed', {ATTR_ENTITY_ID: self._scs_id, ATTR_STATE: command})
#       refreshEverything()

#       _LOGGER.info("controller.schedule_update_ha_state()")
#       self.schedule_update_ha_state()

        packet = {'sender': 'controller', 'signal': 'update'}
        _LOGGER.info("About to send a dispatcher packet...")
## def async_dispatcher_send(hass, signal, *args):
        self.hass.helpers.dispatcher.async_dispatcher_send(DISPATCHER_EVOHOME, packet)

        return


    def async_set_operation_mode(self, operation_mode):
        """Set new target operation mode.

        This method must be run in the event loop and returns a coroutine."""
        _LOGGER.info("async_set_operation_mode(ControllerEntity=%s, mode=%s)", self._id, operation_mode)
        return self.hass.async_add_job(self.set_operation_mode, operation_mode)


    @property
    def device_state_attributes(self):
        """Return the optional state attributes."""
        _LOGGER.info("state_attributes(ControllerEntity=%s)...", self._id)

        _data = {}

#       _LOGGER.info("SUPPORT_OPERATION_MODE=%s", SUPPORT_OPERATION_MODE)
#       _LOGGER.info("(True and SUPPORT_OPERATION_MODE)=%s", (True and SUPPORT_OPERATION_MODE))
        if True and SUPPORT_OPERATION_MODE:
#       if self.supported_features & SUPPORT_OPERATION_MODE:

            _data[ATTR_OPERATION_MODE] = self._current_operation
#           _data[ATTR_OPERATION_MODE] = self.current_operation
#           _LOGGER.info("ATTR_OPERATION_MODE=%s", ATTR_OPERATION_MODE)
#           _LOGGER.info("self._current_operation=%s", self._current_operation)
#           _LOGGER.info("_data[ATTR_OPERATION_MODE]=%s", _data[ATTR_OPERATION_MODE])

#           _LOGGER.info("self._operation_list=%s", self._operation_list)
#           _LOGGER.info("(True and self._operation_list)=%s", (True and self._operation_list))
            if True and self._operation_list:
#           if self.operation_list:
                _data[ATTR_OPERATION_LIST] = self._operation_list
#               _data[ATTR_OPERATION_LIST] = self.operation_list
#               _LOGGER.info("ATTR_OPERATION_LIST=%s", ATTR_OPERATION_LIST)
#               _LOGGER.info("self._operation_list=%s", self._operation_list)
#               _LOGGER.info("_data[ATTR_OPERATION_LIST]=%s", _data[ATTR_OPERATION_LIST])

#       _data = {}
#       _data[ATTR_OPERATION_LIST] = self._operation_list

        _LOGGER.info("state_attributes(): _data=%s", _data)
        return _data


    @property
    def state_attributes(self):
        """Return the optional state attributes."""
        _LOGGER.info("device_state_attributes(ControllerEntity=%s)...", self._id)

        _data = {}

        _LOGGER.info("device_state_attributes(): _data=%s", _data)
        return _data



    @property
    def supported_features(self):
        """Get the list of supported features of the controller."""
## It will likely be the case we need to support Away/Eco/Off modes in the HA fashion
## even though these modes are subtly different - this will allow tight integration
## with the HA landscape / other HA components, e.g. Alexa/Google integration
        _LOGGER.info("supported_features(ControllerEntity=%s): %s", self._id, SUPPORT_OPERATION_MODE)
        return SUPPORT_OPERATION_MODE


    def update(self):
        """Get the latest state (operating mode) of the controller and
        update the state (temp, setpoint) of all children zones.

        (TBA) Also, get the latest schedule of the controller every hour."""
        _LOGGER.info("update(ControllerEntity=%s)", self._id)

## TBA: no provision (yet) for DHW

## If the OAuth token has expired, we need to re-authenticate to get another
        timeout = self.hass.data[DATA_EVOHOME]['tokenExpires']
        timeout = strptime(timeout, _OAUTH_TIMEOUT_FORMAT)
        if datetime.now() > datetime.fromtimestamp(mktime(timeout)):
            _LOGGER.info("OAuth token expired at %s. Re-connecting to the Honeywell web API now...", timeout)
            try:
# Open a session to Honeywell's servers
                _LOGGER.info("Calling client API: _login())...")
#               self.client.access_token = None
                self.client._login()
# the OAuth token needs refreshing after 1hr, but evohome-client provides no means of doing so...
                timeout = datetime.now() + timedelta(seconds = _OAUTH_TIMEOUT_SECONDS)
                timeout = timeout.strftime(_OAUTH_TIMEOUT_FORMAT)
                self.hass.data[DATA_EVOHOME]['tokenExpires'] = timeout

                _LOGGER.info(" - connected OK, OAuth token now expires at %s.", timeout)

            except:
                _LOGGER.error("Failed to reconnect to the Honeywell web API!")
                raise


## Update the data: get the latest modes/temps

        ec_status = _returnTempsAndModes(self.client)  # get latest modes/temps
        _LOGGER.debug("ec_api.status() = %s", ec_status)

        self.hass.data[DATA_EVOHOME]['status'] = ec_status

        self._current_operation = ec_status['systemModeStatus']['mode']
        _LOGGER.info("(UPDATE) Current operating_mode (of location/controller) is: %s", self._current_operation)

        self.hass.data[DATA_EVOHOME]['lastUpdated'] = datetime.now().strftime(_OAUTH_TIMEOUT_FORMAT)
#       self.hass.data[DATA_EVOHOME]['schedule'] = self.client.zone_schedules_backup()

        _LOGGER.debug("self.hass.data[DATA_EVOHOME]['tokenExpires'] = %s", self.hass.data[DATA_EVOHOME]['tokenExpires'])
        _LOGGER.debug("self.hass.data[DATA_EVOHOME]['installation'] = %s", self.hass.data[DATA_EVOHOME]['installation'])
        _LOGGER.debug("self.hass.data[DATA_EVOHOME]['status'] = %s", self.hass.data[DATA_EVOHOME]['status'])
#       _LOGGER.debug("self.hass.data[DATA_EVOHOME]['schedules'] = %s", self.hass.data[DATA_EVOHOME]['schedules'])
#       _LOGGER.debug("self.client.full_installation() = %s", self.client.full_installation())





class evoZoneEntity(evoEntity, ClimateDevice):
    """Honeywell evohome Zone Entity base."""

    def __init__(self, hass, client, zone):
        """Initialize the evoEntity."""
        super().__init__(hass, client, zone)

        self._id = int(zone['zoneId'])
        self._name = zone['name']

        _LOGGER.debug("__init__(controller=%s)",
            self._name + " [" + self._name + "]",
            )

#       self._operation_list = []  # done in super()
        for mode in zone['heatSetpointCapabilities']['allowedSetpointModes']:
            self._operation_list.append(mode)
#       self._operation_list = [ "FollowSchedule", "TemporaryOverride", "PermanentOverride" ]  ## explicitly set for a 'nice' order

## Setpoints are usually 5-35C, but zones can be configured inside these values.
        self._min_temp = zone['heatSetpointCapabilities']['minHeatSetpoint']
        self._max_temp = zone['heatSetpointCapabilities']['maxHeatSetpoint']

        self._current_temperature = None
        self._target_temperature  = None
        self._current_operation   = None

## Now get the status...
        _LOGGER.debug("For zone %s (%s)", self._id, self._name)
        for temp in hass.data[DATA_EVOHOME]['status']['zones']:
            _LOGGER.debug(" - is it slave %s (%s)?", temp['zoneId'], temp['name'])

            if int(temp['zoneId']) == int(self._id):
                self._current_temperature = temp['temperatureStatus']['temperature']
                self._target_temperature  = temp['heatSetpointStatus']['targetTemperature']
                self._current_operation   = temp['heatSetpointStatus']['setpointMode']

                _LOGGER.debug(" - matched: temp: %s, target: %s, mode: %s.",
                    self._current_temperature,
                    self._target_temperature,
                    self._current_operation
                    )
                break

        return True


    @property
    def should_poll(self):
#   def poll(self):
        """Zones should not be polled?, the controller will maintain state data."""
        return True


    @property
    def force_update(self):
#   def poll(self):
        """TBA."""
        return False


    @property
    def state(self):
        """Return the zone's current state (usually, its operation mode).

        A zone's state is usually its operation mode, but they may enter
        OpenWindowMode autonomously."""
        _LOGGER.debug("state(ZoneEntity=%s): %s...", self._id, self._current_operation)

        if self._target_temperature == 5:
#           if EVO_FOLLOW and sched-temp <> 5:
            _LOGGER.debug("state(ControllerEntity=%s): changed from %s to %s.", self._id, EVO_FOLLOW, EVO_OPENWINDOW)
            return EVO_OPENWINDOW
        else:
            _LOGGER.debug("state(ControllerEntity=%s): unchanged as %s.", self._id, self._current_operation)
            return self._current_operation


    @property
    def current_operation(self):
        """Return the current operation mode of the zone."""
        return self._current_operation


    @property
    def device_state_attributes(self):
        """Return the optional state attributes."""
        _LOGGER.debug("state_attributes(ControllerEntity=%s)...", self._id)

        _data = {}
        _data[ATTR_OPERATION_MODE] = self._current_operation
        _data[ATTR_OPERATION_LIST] = self._operation_list

        _LOGGER.debug("state_attributes(): _data=%s", _data)
        return _data


    @property
    def name(self):
        """Get the name of the zone."""
        return self._name


    @property
    def icon(self):
        """Return the icon to use in the frontend UI."""
        return "mdi:radiator"


    @property
    def operation_list(self):
        """Return the list of available operation modes."""
        return self._operation_list


#   def set_operation_mode(self: ClimateDevice, operation: str, setpoint=None, until=None) -> None:
    def set_operation_mode(self, operation_mode, setpoint=None, until=None):
        """Set the operating mode for the zone."""
        _LOGGER.debug("for zone = %s: set_operation_mode(operation_mode=%s, setpoint=%s, until=%s)", self._name, operation_mode, setpoint, until)

#       zone = self.client._get_single_heating_system.zones_by_id[str(self._id)])
        zone = self.client.locations[0]._gateways[0]._control_systems[0].zones_by_id[str(self._id)]

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
            if z['zoneId'] == str(self._id):
                z['heatSetpointStatus']['setpointMode'] = self._operating_mode
                z['heatSetpointStatus']['targetTemperature'] = self._target_temperature

#       _LOGGER.debug("refreshEverything(): controller.schedule_update_ha_state()")
        self.schedule_update_ha_state()



    def async_set_operation_mode(self, operation_mode):
        """Set new target operation mode.

        This method must be run in the event loop and returns a coroutine."""
        return self.hass.async_add_job(self.set_operation_mode, operation_mode)


    @property
    def supported_features(self):
        """Get the list of supported features of the zone."""
        return SUPPORT_TARGET_TEMPERATURE | SUPPORT_OPERATION_MODE


    @property
    def precision(self):
        """Return the precision of the system."""
#       if not ?using v1 API? == TEMP_CELSIUS:
#           return PRECISION_HALVES
        return PRECISION_TENTHS


#   @property
#   def unit_of_measurement(self):
#       """Return the unit of measurement to display."""
#       return self.hass.config.units.temperature_unit
#       return self.temperature_unit

    @property
    def temperature_unit(self):
        """Get the unit of measurement of the controller."""
        return TEMP_CELSIUS


    def set_temperature(self, **kwargs):
        """Set a target temperature (setpoint) for the zone."""
        _LOGGER.debug("Just started: set_temperature(%s, %s)", self._name, kwargs)
#       for name, value in kwargs.items():
#          _LOGGER.debug('%s = %s', name, value)

        temperature = kwargs.get(ATTR_TEMPERATURE)
        if temperature is None:
#          _LOGGER.debug("set_temperature(temperature=%s) is invalid (None)!", temperature)
            return
        if temperature > self._max_temp:
#          _LOGGER.debug("set_temperature(temperature=%s) is invalid (above maximum)!", temperature)
            return
        if temperature < self._min_temp:
#          _LOGGER.debug("set_temperature(temperature=%s) is invalid (below minimum)!", temperature)
            return

        _LOGGER.debug("ZX Calling API: zone.set_temperature(%s)...", temperature)
#       zone = self.client._get_single_heating_system.zones[self._name]
        zone = self.client.locations[0]._gateways[0]._control_systems[0].zones[self._name]
        zone.set_temperature(temperature)

        self._operating_mode = "PermanentOverride"
        self._target_temperature = temperature

# now tell HA that things have changed...
#       self.schedule_update_ha_state()


    def async_set_temperature(self, **kwargs):
        """Set new target temperature.

        This method must be run in the event loop and returns a coroutine."""
        _LOGGER.debug("Just started: async_set_temperature(%s, %s)", self._name, kwargs)
        return self.hass.async_add_job(
            ft.partial(self.set_temperature, **kwargs)
            )


    @property
    def current_temperature(self):
        """Return the current temperature."""
        return self._current_temperature


    @property
    def target_temperature(self):
        """Return the temperature we try to reach."""
#       if self._is_dhw:
#           return None
        return self._target_temperature


    @property
    def target_temperature_step(self):
        """Return the supported step of target temperature."""
        return PRECISION_HALVES


    @property
    def min_temp(self):
        """Return the minimum temperature."""
        return self._min_temp


    @property
    def max_temp(self):
        """Return the maximum temperature."""
        return self._max_temp


    def update(self):
        """Get the latest state (operating mode, temperature) of a zone."""
        _LOGGER.debug("Just started: update(zone = %s)",
            str(self._id) + " [" + self._name + "]"
            )

        ec_status = self.hass.data[DATA_EVOHOME]['status']
#       _LOGGER.debug("ec_status = %s.", ec_status)
        if ec_status == {}:
            _LOGGER.error("ec_status = %s.", ec_status)
            return

        _LOGGER.debug("Before: zone = %s, temp: %s, target: %s, mode: %s.",
            str(self._id) + " [" + self._name + "]",
            self._current_temperature,
            self._target_temperature,
            self._current_operation
            )

        for zone in ec_status['zones']:
            if int(zone['zoneId']) == int(self._id):
                self._current_temperature = zone['temperatureStatus']['temperature']
                self._target_temperature = zone['heatSetpointStatus']['targetTemperature']
                self._current_operation = zone['heatSetpointStatus']['setpointMode']

                _LOGGER.debug("Change: zone = %s, temp: %s, target: %s, mode: %s.",
                    str(self._id) + " [" + self._name + "]",
                    self._current_temperature,
                    self._target_temperature,
                    self._current_operation
                    )
                break


        _LOGGER.debug("After:  zone = %s, temp: %s, target: %s, mode: %s.",
            str(self._id) + " [" + self._name + "]",
            self._current_temperature,
            self._target_temperature,
            self._current_operation
            )



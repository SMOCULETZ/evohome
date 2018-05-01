"""
Support for Honeywell (EU-only) Evohome installations: One controller and multiple zones.

To install this custom component, copy it to ${HASS_CONFIG_DIR}/custom_components/climate/evohome.py.
The configuration.yaml as below...

climate:
  - platform: evohome
    username: !secret_evohome_username
    password: !secret_evohome_password
    scan_interval: 300
"""
### Based upon: https://gist.github.com/namadori/3f3a15bbbae4f8783394993c148cb555 (with thanks)

### This implements a climate component for the evohome controller only. In future,
#   it may be better for a 'group' of 1 state object, and 1+ climate components?
#  - see: https://community.home-assistant.io/t/components-creating-and-updating-groups/11566


import logging
import socket
from datetime import datetime, timedelta
from time import sleep

import requests
import voluptuous as vol

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
    CONF_USERNAME, CONF_PASSWORD,
    TEMP_CELSIUS, TEMP_FAHRENHEIT,
    ATTR_TEMPERATURE,
    PRECISION_HALVES, PRECISION_TENTHS
    )

## https://www.home-assistant.io/developers/component_deps_and_reqs/
#  https://github.com/home-assistant/home-assistant.github.io/pull/5199
REQUIREMENTS = ['evohomeclient==0.2.5']

## https://www.home-assistant.io/components/logger/
_LOGGER = logging.getLogger(__name__)

PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend({
    vol.Required(CONF_USERNAME): cv.string,
    vol.Required(CONF_PASSWORD): cv.string
#    vol.Optional(CONF_AWAY_TEMPERATURE,
#                 default=DEFAULT_AWAY_TEMPERATURE): vol.Coerce(float),
#    vol.Optional(CONF_COOL_AWAY_TEMPERATURE,
#                 default=DEFAULT_COOL_AWAY_TEMPERATURE): vol.Coerce(float),
#    vol.Optional(CONF_HEAT_AWAY_TEMPERATURE,
#                 default=DEFAULT_HEAT_AWAY_TEMPERATURE): vol.Coerce(float),
})


def setup_platform(hass, config, add_devices, discovery_info=None):
    """Set up a Honeywell evoTouch heating system (1 controller and multiple zones)."""

    _LOGGER.info("Started: setup_platform(evohome), unit: %s", TEMP_CELSIUS)

### Need to add code to exclude US-based systems...
    
    username = config.get(CONF_USERNAME)
    password = config.get(CONF_PASSWORD)

# Use the evohome-client v2 API  (v2 of EC API uses OAuth)
#   from evohomeclient  import EvohomeClient as EvohomeClientVer1  ## uses v1 of the api
    from evohomeclient2 import EvohomeClient as EvohomeClient      ## uses v2 of the api

    try:
# Open a session to Honeywell's servers
        ec_api = EvohomeClient(username, password, debug=False)
        _LOGGER.debug("Connected OK by logging into the Honeywell web API.")

    except socket.error:
        _LOGGER.error("Failed to connect (socket.error) whilst logging into the Honeywell web API.")
        return False


# Although Installations, Gateways, ControlSystems are 1:M, & 1:M, evohome-client assumes 1:1:1??
    _LOGGER.debug("Found evohome controller:")
    _LOGGER.debug(" - ec_api.system_id: {0}".format(ec_api.system_id))
    _LOGGER.debug(" - ec_api.account_info: {0}".format(ec_api.account_info))
    _LOGGER.debug(" - ec_api.installation_info[0]: {0}".format(ec_api.installation_info[0]))

# Determine the system configuration, this is more efficient than ec_api.full_installation()
    ec_loc = ec_api.installation_info[0] ## only 1 location for now...

    location = ec_loc['locationInfo']
    controller = ec_loc['gateways'][0]['temperatureControlSystems'][0]

# Use Location ID, or System ID as ID??? evohome-client uses System ID
    _LOGGER.info("Found Controller: id: %s, type: %s, name: %s", location['locationId'], controller['modelType'], location['name'])

# Collect each (child) zone as a (climate component) device
    evo_devices = []
    for zone in controller['zones']:
        _LOGGER.debug("Found Zone: id: %s, type: %s, name: %s", zone['zoneId'], zone['zoneType'], zone['name'])
#       if zone['zoneType'] in [ "RadiatorZone", "ZoneValves" ]:  # what about DHW - how to exclude?
        child = evoZone(ec_api, zone)
        evo_devices.append(child)  # add this zone to the list of devices

# Collect the (parent) controller (a merge of location & controller)
#   parent = evoController(ec_api, controller, location, evo_devices)  ## (ec_api, device, identifier, children[])
    parent = evoController(ec_api, location, evo_devices)              ## (ec_api, device, children[])

# Create them all in one batch - do I need to use: itertools.chain(a, b)
## what does the 'True' do: add_devices(evo_devices, True)? initial update(), it seems it takes too long?
    add_devices([ parent ] + evo_devices, False)  ## initial update: doesn't work here

#   parent.update()  ## initial update: doesn't work here

    return True



class evoController(ClimateDevice):
    """Representation of a Honeywell evohome zone (thermostat)."""

#   def __init__(self, client, controlSystem, locationInfo, childZones):
    def __init__(self, client, locationInfo, childZones):
        """Initialize the controller."""
        _LOGGER.debug("Creating Controller (__init__): id: %s, name: %s", locationInfo['locationId'], locationInfo['name'])

        self.client = client
        self._id = locationInfo['locationId']
        self._name = "_" + locationInfo['name'] + " (controller)"  ## a hack to put the controller on top
        self._childZones = childZones

        self._supported_features = SUPPORT_OPERATION_MODE
        self._operation_list = ["AutoWithReset", "Auto", "AutoWithEco", "DayOff", "Away", "HeatingOff", "Custom"]
        self._operating_mode = None

# apparently, HA requires a temp unit for all climate devices (even those without a temp)
#       self._temperature_unit = TEMP_CELSIUS

#       self._master = master
#       self._is_dhw = False
#       self._away_temp = 10
#       self._away = False

## See: https://developers.home-assistant.io/docs/en/creating_platform_code_review.html
# - it says: Do not call update() in constructor, use add_devices(devices, True) instead.
        self.update() ## initial update: does work here
## what about: the_zone.schedule_update_ha_state()       


#   @property
#   def icon(self):
#       """Return the icon to use in the frontend UI, if any."""
#       return "mdi:nest-thermostat"  ## is usually: mdi:nest-thermostat

    @property
    def supported_features(self):
        """Get the list of supported features of the controller."""
        _LOGGER.debug("Just started: supported_features(controller)")
## It will likely be the case we need to support Away/Eco/Off modes in the HA fashion 
## even though these modes are subtly different - this will allow tight integration
## with the HA landscape / other HA components, e.g. Alexa/Google integration
#       supported = (SUPPORT_TARGET_TEMPERATURE)
#       if hasattr(self.client, ATTR_SYSTEM_MODE):
#           supported |= SUPPORT_OPERATION_MODE
        return self._supported_features

    @property
    def name(self):
        """Get the name of the controller."""
        _LOGGER.debug("Just started: name(zone)")
        return self._name

    @property
    def temperature_unit(self):
        """Get the unit of measurement of the controller."""
### This must be implemented despite SUPPORT_FLAGS = SUPPORT_OPERATION_MODE (only)
# see: https://github.com/home-assistant/home-assistant/blob/dev/homeassistant/helpers/entity.py
        _LOGGER.debug("Just started: temperature_unit(controller)")
#       raise NotImplementedError()  ## doesn't help
#       return None                  ## Causes ValueError: None is not a recognized temperature unit.
        return TEMP_CELSIUS

    @property
    def operation_list(self):
        """Get the list of available operations fro the controller."""
        _LOGGER.debug("Just started: operation_list(controller)")

#       op_list = []
#       for mode in EVOHOME_STATE_MAP:
#           op_list.append(EVOHOME_STATE_MAP.get(mode))
#       return op_list
        return self._operation_list


    @property
    def current_operation(self: ClimateDevice) -> str:
        """Get the current operating mode of the controller."""
        _LOGGER.debug("Just started: current_operation(controller)")
#       return getattr(self.client, ATTR_SYSTEM_MODE, None)
        return self._operating_mode


    def set_operation_mode(self: ClimateDevice, operation: str) -> None:
        """Set the operating mode of the controller.  Note that 'AutoWithReset may not be a
        mode in itself: instead, it _should_ lead to 'Auto' mode after resetting all the zones
        to 'FollowSchedule'.  'HeatingOff' simply sets setpoints to a minimum value."""
        _LOGGER.info("Just started: set_operation_mode(controller, %s), operation")

### Controller: operations vs (operating) modes...

# "AutoWithReset" _should_ lead to "Auto" mode (but doesn't), after resetting all the zones to "FollowSchedule"
        if operation == "AutoWithReset":  ## a private function in the client API (it is not exposed)
        ## here, we call 
          OPERATING_MODE_AUTOWITHRESET = 5
          self.client.locations[0]._gateways[0]._control_systems[0]._set_status(OPERATING_MODE_AUTOWITHRESET)
          self._operating_mode = "Auto"  ## this doesn't work

## I'm not sure if this works either...          
          for child in self._childZones:
            _LOGGER.debug("for child %s (%s)...", child._id, child._name)
            child._operating_mode = "FollowSchedule"
            child.update()
          
        else:
          self._operating_mode = operation
# There is no EvohomeClient.set_status_reset exposed via the client API, so
# we're using EvohomeClient...ControlSystem._set_status(5) instead.
          functions = {
#           'AutoWithReset': self.client.locations[0]._gateways[0]._control_systems[0]._set_status(5),
            'Auto':          self.client.set_status_normal,
            'AutoWithEco':   self.client.set_status_eco,
            'DayOff':        self.client.set_status_dayoff,
            'Away':          self.client.set_status_away,
            'HeatingOff':    self.client.set_status_heatingoff,
            'Custom':        self.client.set_status_custom
            }

# before calling func(), should check OAuth token still viable, but how?
          func = functions[operation]
          _LOGGER.debug("set_operation_mode(), func = %s), func")
          func()


# this should be used to update zones after change effected to controller, it
# doesn't see to work, though...
        sleep(10)
        self.update()


    @property
    def is_away_mode_on(self):
        """Return true if Away mode is on."""
        _LOGGER.debug("Just started: is_away_mode_on(controller)")
        return self._away

    def turn_away_mode_on(self):
        """Turn away on for the location.
        Honeywell does have a proprietary away mode, but it doesn't really work
        the way it should. For example: If you set a temperature manually
        it doesn't get overwritten when away mode is switched on.
        """
        _LOGGER.debug("Just started: turn_away_mode_on(controller)")
        self._away = True
        self.client.set_status_away() # Heating and hot water off

# this should be used to update zones after change effected to controller
#       self.update()

    def turn_away_mode_off(self):
        """Turn away off for the location."""
        _LOGGER.debug("Just started: turn_away_mode_off(controller)")
        self._away = False
        self.client.set_status_normal()

# this should be used to update zones after change effected to controller
#       self.update()

    def update(self):
        """Get the latest state (operating mode) of the controller and
        update the state (temp, setpoint) of all children zones."""
        _LOGGER.debug("Just started: update(controller)")

#       if data['thermostat'] == 'DOMESTIC_HOT_WATER':
#           self._name = 'Hot Water'
#           self._is_dhw = True
#       else:
#           self._name = data['name']
#           self._is_dhw = False

#           status=self.client.locations[0].status()
#           _LOGGER.debug(status)

#           tcs=status['gateways'][0]['temperatureControlSystems'][0]
#           currentmode=tcs['systemModeStatus']['mode']
#           self.client.system_mode = currentmode
            #_LOGGER.error(status)


        try:
            # Only refresh if this is the "master" device,
            # others will pick up the cache
#           ec_tmp = self.client1.temperatures()
            ec_tmp = self.client.locations[0].status()

        except TypeError:
        # this is the error - does this code skip a update cycle?
            _LOGGER.error("Update (of location) failed: TypeError (usually because OAuth token has timed out?)")

### http://www.automatedhome.co.uk/vbulletin/showthread.php?3863-Decoded-EvoHome-API-access-to-control-remotely&p=20192&viewfull=1#post20192
# curl -s -v https://rs.alarmnet.com/TotalConnectComfort/Auth/OAuth/Token
#     -H "Authorization: Basic YjAxM2FhMjYtOTcyNC00ZGJkLTg4OTctMDQ4YjlhYWRhMjQ5OnRlc3Q="
#     --data "grant_type=refresh_token&scope=EMEA-V1-Basic+EMEA-V1-Anonymous+EMEA-V1-Get-Current-User-Account&refresh_token=<REFRESH_TOKEN>"

            self.client.access_token = None
            self.client._login()
            return

        ec_tcs = ec_tmp['gateways'][0]['temperatureControlSystems'][0]
#       _LOGGER.debug(ec_tcs)

#       self.client.system_mode = ec_tcs['systemModeStatus']['mode']
        self._operating_mode = ec_tcs['systemModeStatus']['mode']
        _LOGGER.debug("Current system mode (of location/controller) is: %s", self._operating_mode)

        for child in self._childZones:
            _LOGGER.debug("for child {0} ({1})...".format(child._id, child._name))
            for zone in ec_tcs['zones']:
                _LOGGER.debug(" - is it zone {0} ({1})?".format(zone['zoneId'], zone['name']))
                if zone['zoneId'] == child._id:
                    child._current_temperature = zone['temperatureStatus']['temperature']
                    child._target_temperature = zone['heatSetpointStatus']['targetTemperature']
                    child._operating_mode = zone['heatSetpointStatus']['setpointMode']

                    _LOGGER.debug("Zone: %s, Temp: %s, Setpoint %s, Mode: %s", child._name, child._current_temperature, child._target_temperature, child._operating_mode)
#                   child.update()
                    break


        try:
            from evohomeclient  import EvohomeClient as EvohomeClientVer1  ## uses v1 of the api
            ev_api = EvohomeClientVer1(self.client.username, self.client.password)
            zones = list(ev_api.temperatures(force_refresh=True)) # use list() to convert from a generator

            for child in self._childZones:
                _LOGGER.debug("for child {0} ({1})...".format(child._id, child._name))
                for zone in zones:
                    _LOGGER.debug(" - is it zone {0} ({1})?".format(zone['id'], zone['name']))
                    if int(zone['id']) == int(child._id):
                        _LOGGER.debug(" - for child {0}, zone {1}, temp {2}...".format(child._id, zone['id'], zone['temp']))
                        child._current_temperature = zone['temp']
                        break

        except:
            _LOGGER.error("Failed to connect to the Honeywell web v1 API (for higher precision temps).")
            raise

        for child in self._childZones:
            _LOGGER.info("update(controller) - for child {0} ({1}), temp = {2}.".format(child._id, child._name, child._current_temperature))


            
            

class evoZone(ClimateDevice):
    """Representation of a Honeywell evohome zone (thermostat)."""

    def __init__(self, client, zone):
        """Initialize the zone."""
        _LOGGER.debug("Creating zone (__init__): id %s, name: %s", zone['zoneId'], zone['name'])

        self.client = client
        self._id = zone['zoneId']
        self._name = zone['name']

## Zones have no Away mode, nor can they be Off
#       self._supported_features = (SUPPORT_TARGET_TEMPERATURE | SUPPORT_OPERATION_MODE)
#       self._operation_list = zone['heatSetpointCapabilities']['allowedSetpointModes']
        self._operating_mode = None

## usu.: 5-35C, but zones can be configured to be inside these values
        self._min_temp = zone['heatSetpointCapabilities']['minHeatSetpoint']
        self._max_temp = zone['heatSetpointCapabilities']['maxHeatSetpoint']

        self._current_temperature = None
        self._target_temperature = None


    @property
    def name(self):
        """Get the name of the zone."""
        _LOGGER.debug("Just started: name(%s)", self._name)
        return self._name

    @property
    def icon(self):
        """Return the icon to use in the frontend UI for the zone."""
        return "mdi:radiator"  ## default is: mdi:nest-thermostat

    @property
    def supported_features(self):
        """Get the list of supported features of the zone."""
        _LOGGER.debug("Just started: supported_features(%s)", self._name)
        return SUPPORT_TARGET_TEMPERATURE | SUPPORT_OPERATION_MODE  ## zones do not support Away (or Off) mode

    @property
    def operation_list(self):
        """Get the list of available operations for the zone."""
        _LOGGER.debug("Just started: operation_list(%s)", self._name)
        return [ "FollowSchedule", "TemporaryOverride", "PermanentOverride" ]  ## explicitly set for a 'nice' order

    @property
    def temperature_unit(self):
        """Get the unit of measurement of the current/target temperatures of the zone."""
        _LOGGER.debug("Just started: temperature_unit(%s)", self._name)
        return TEMP_CELSIUS

    @property
    def precision(self):
        """Get the precision of the current temperature of the zone."""
#       return PRECISION_HALVES  # if using v2 of api
#       return 0.01              # if using v1 of api, but...
        return PRECISION_TENTHS  # HA doesn't support PRECISION_HUNDRETHS
        
    @property
    def target_temperature_step(self):
        """Return the supported step of target temperature (setpoint) of the zone."""
        return PRECISION_HALVES

    @property
    def min_temp(self):
        """Return the minimum target temperature (setpoint) of the zone."""
#       return convert_temperature(7, TEMP_CELSIUS, self.temperature_unit)
        return self._min_temp
        
    @property
    def max_temp(self):
        """Return the maximum target temperature (setpoint) of the zone."""
#       return convert_temperature(35, TEMP_CELSIUS, self.temperature_unit)
        return self._max_temp
        
    @property
    def target_temperature(self):
        """Get the current target temperature (setpoint) of the zone."""
        _LOGGER.debug("Just started: target_temperature(%s)", self._name)
#       if self._is_dhw:
#           return None
        return self._target_temperature

    @property
    def current_temperature(self):
        """Get the current temperature of the zone."""
        _LOGGER.debug("Just started: current_temperature({0}), temp = {1}".format(self._name, self._current_temperature))
        return self._current_temperature
        
        
    def set_temperature(self, **kwargs):
        """Set a target temperature (setpoint) for the zone."""
        _LOGGER.debug("Just started: set_temperature({0}, {1})".format(self._name, kwargs))
#       for name, value in kwargs.items():
#          _LOGGER.debug('{0} = {1}'.format(name, value))

        temperature = kwargs.get(ATTR_TEMPERATURE)
        if temperature is None:
            return        
        if temperature > self._max_temp:
            return
        if temperature < self._min_temp:
            return
            
        _LOGGER.debug("ZX Calling API: zone.set_temperature({0})".format(temperature))
        zone = self.client.locations[0]._gateways[0]._control_systems[0].zones[self._name]
        zone.set_temperature(temperature)
        
        self._operating_mode = "PermanentOverride"
        self._target_temperature = temperature

        
    @property
    def current_operation(self: ClimateDevice) -> str:
        """Get the current operating mode of the zone."""
        _LOGGER.debug("Just started: current_operation(%s)", self._name)
#       return getattr(self.client, ATTR_SYSTEM_MODE, None)
        return self._operating_mode


    def set_operation_mode(self: ClimateDevice, operation: str, setpoint=None, until=None) -> None:
        """Set the operating mode for the zone."""
        _LOGGER.info("for zone = %s: set_operation_mode(%s, %s, %s)", self._name, operation, setpoint, until)

#       zone = self.client.locations[0]._gateways[0]._control_systems[0].zones_by_id['3432521'])
        zone = self.client.locations[0]._gateways[0]._control_systems[0].zones_by_id[self._id]

        _LOGGER.debug("for zone = {0} (self), {1} (zone)".format(self, zone))

        if operation == 'FollowSchedule':
            _LOGGER.debug("ZX Calling API: zone.cancel_temp_override()")
            zone.cancel_temp_override(zone)

        else:
            if setpoint == None:
                setpoint = self._target_temperature

            if operation == 'PermanentOverride':
                _LOGGER.debug("ZX Calling API: zone.set_temperature({0})".format(temperature))
                zone.set_temperature(setpoint)  ## override target temp indefinitely
                
            else:
                if until == None:
#                   UTC_OFFSET_TIMEDELTA = datetime.now() - datetime.utcnow()
                    until = datetime.utcnow() + timedelta(1/24) ## use .utcnow() or .now() ??
                
                if operation == 'TemporaryOverride':
                    _LOGGER.debug("ZX Calling API: zone.set_temperature({0}, {1})".format(temperature, until))
                    zone.set_temperature(setpoint, until)  ## override target temp (for a hour)


        self._operating_mode = operation
        self._target_temperature = setpoint


    def update(self):
        """Get the latest state (temperature, setpoint, mode) of the zone."""
        _LOGGER.debug("Just started: update(%s)", self._name)
        return

# No updates here - the controller updates all it zones
# - this use API calls, 1 per group of zones, rather than 1 each

#       child._current_temperature = zone['temp']
#       child._target_temperature = zone['setpoint']
#       child._current_operation = "unknown"


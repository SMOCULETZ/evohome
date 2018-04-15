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

from homeassistant.components.climate import (
    ClimateDevice, PLATFORM_SCHEMA,
    ATTR_OPERATION_MODE, ATTR_OPERATION_LIST,
    SUPPORT_TARGET_TEMPERATURE,
    SUPPORT_OPERATION_MODE)

from homeassistant.const import (
    CONF_USERNAME, CONF_PASSWORD,
    TEMP_CELSIUS, TEMP_FAHRENHEIT,
    ATTR_TEMPERATURE,
    PRECISION_HALVES
    )

REQUIREMENTS = ['evohomeclient==0.2.5']

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

### Need to add code to exclude US-based systems
    
    username = config.get(CONF_USERNAME)
    password = config.get(CONF_PASSWORD)

# Use the evohome-client v2 API  (v2 of EC API uses OAuth)
#   from evohomeclient  import EvohomeClient as EvohomeClientVer1  ## uses v1 of the api
    from evohomeclient2 import EvohomeClient as EvohomeClient      ## uses v2 of the api

    try:
# Open a session to Honeywell's servers
        ec_api = EvohomeClient(username, password)

    except socket.error:
        _LOGGER.error("Failed to connect (socket.error) whilst logging into the Honeywell web API.")
        return False

    _LOGGER.info("Connected OK by logging into the Honeywell web API.")


# Determine the system configuration- ec_api.full_installation()
    ec_loc = ec_api.full_installation()  ## using: ec_api.installation() causes problems

# Although Installations, Gateways, ControllSystems are 1:M, & 1:M, we assume 1:1:1
    location = ec_loc['locationInfo']
    controller = ec_loc['gateways'][0]['temperatureControlSystems'][0]

    _LOGGER.info("Found Controller: id: %s, name: %s, type: %s", location['locationId'], location['name'], controller['modelType'], )

# Collect each (child) zone as a (climate component) device
    evo_devices = []
    for zone in controller['zones']:
        _LOGGER.info("Found Zone: id: %s, name: %s, type: %s", zone['zoneId'], zone['name'], zone['zoneType'])
        if zone['zoneType'] == "RadiatorZone":
            child = evoZone(ec_api, zone)
            evo_devices.append(child)  ## add this zone to the list of devices

# Collect the (parent) controller (a merge of location & controller)
    parent = evoController(ec_api, controller, location, evo_devices)  ## (ec_api, device, identifier, children)


# Create them all in one batch - do I need to use: itertools.chain(a, b)
## what does the 'true' do: add_devices(evo_devices, True)? initial update(), it seems it takes too long?
    add_devices([ parent ] + evo_devices, False)  ## initial update: doesn't work here
#   add_devices(evo_devices, True)
#   add_devices([ parent] )

#   parent.update()  ## initial update: doesn't work here

    return True



class evoController(ClimateDevice):
    """Representation of a Honeywell evohome zone (thermostat)."""

    def __init__(self, client, controlSystem, locationInfo, childZones):
        """Initialize the zone."""
        _LOGGER.info("Creating Controller (__init__): id: %s, name: %s", locationInfo['locationId'], locationInfo['name'])

        self.client = client
        self._id = locationInfo['locationId']
        self._name = "_" + locationInfo['name'] + " (controller)"  ## a hack to put the controller on top
        self._childZones = childZones

        self._supported_features = SUPPORT_OPERATION_MODE
        self._operation_list = ["AutoWithReset", "Auto", "AutoWithEco", "DayOff", "Away", "HeatingOff", "Custom"]
        self._operating_mode = None

# apparently, HA requires a temp unit for all climate devices (even those without a temp)
        self._temperature_unit = TEMP_CELSIUS

#       self._master = master
#       self._is_dhw = False
#       self._away_temp = 10
#       self._away = False

        self.update() ## initial update: does work here


    @property
    def icon(self):
        """Return the icon to use in the frontend UI, if any."""
        return "mdi:nest-thermostat"  ## is usually: mdi:nest-thermostat

    @property
    def supported_features(self):
        """Get the list of supported features of the controller."""
        _LOGGER.info("Just started: supported_features(controller)")
#       supported = (SUPPORT_TARGET_TEMPERATURE)
#       if hasattr(self.client, ATTR_SYSTEM_MODE):
#           supported |= SUPPORT_OPERATION_MODE
        return self._supported_features

    @property
    def name(self):
        """Get the name of the controller."""
        _LOGGER.info("Just started: name(zone)")
        return self._name

    @property
    def temperature_unit(self):
        """Get the unit of measurement of the controller."""
### This must be implemented despite SUPPORT_FLAGS = SUPPORT_OPERATION_MODE (only)
# see: https://github.com/home-assistant/home-assistant/blob/dev/homeassistant/helpers/entity.py
        _LOGGER.debug("Just started: temperature_unit(controller)")
#       return None  ## Causes exception- ValueError: None is not a recognized temperature unit.
        return self._temperature_unit

    @property
    def operation_list(self):
        """Get the list of available operations fro the controller."""
        _LOGGER.info("Just started: operation_list(controller)")

#       op_list = []
#       for mode in EVOHOME_STATE_MAP:
#           op_list.append(EVOHOME_STATE_MAP.get(mode))
#       return op_list
        return self._operation_list


    @property
    def current_operation(self: ClimateDevice) -> str:
        """Get the current operating mode of the controller."""
        _LOGGER.info("Just started: current_operation(controller)")
#       return getattr(self.client, ATTR_SYSTEM_MODE, None)
        return self._operating_mode


    def set_operation_mode(self: ClimateDevice, operation: str) -> None:
        """Set the operating mode of the controller.  Note that 'AutoWithReset is not a
        mode in itself: instead, it leads to 'Auto' mode after resetting all the zones
        to 'FollowSchedule'.  'HeatingOff' simply sets setpoints to a minimum value."""
        _LOGGER.info("Just started: set_operation_mode(controller, %s), operation")

### Controller: operations vs (operating) modes...

# "AutoWithReset" leads to "Auto" mode, after resetting all the zones to "FollowSchedule"
        if operation == "AutoWithReset":
          self._operating_mode = "Auto"
          self.client.locations[0]._gateways[0]._control_systems[0]._set_status(5)
          
          for child in self._childZones:
            _LOGGER.info("for child %s (%s)...", child._id, child._name)
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
          _LOGGER.info("set_operation_mode(), func = %s), func")
          func()


# this should be used to update zones after change effected to controller, it
# doesn't see to work, though...
        sleep(10)
        self.update()


    @property
    def is_away_mode_on(self):
        """Return true if Away mode is on."""
        _LOGGER.info("Just started: is_away_mode_on(controller)")
        return self._away

    def turn_away_mode_on(self):
        """Turn away on for the location.
        Honeywell does have a proprietary away mode, but it doesn't really work
        the way it should. For example: If you set a temperature manually
        it doesn't get overwritten when away mode is switched on.
        """
        _LOGGER.info("Just started: turn_away_mode_on(controller)")
        self._away = True
        self.client.set_status_away() # Heating and hot water off

# this should be used to update zones after change effected to controller
#       self.update()

    def turn_away_mode_off(self):
        """Turn away off for the location."""
        _LOGGER.info("Just started: turn_away_mode_off(controller)")
        self._away = False
        self.client.set_status_normal()

# this should be used to update zones after change effected to controller
#       self.update()

    def update(self):
        """Get the latest state (operating mode) of the controller and
        update the state (temp, setpoint) of all children zones."""
        _LOGGER.info("Just started: update(controller)")

#       if data['thermostat'] == 'DOMESTIC_HOT_WATER':
#           self._name = 'Hot Water'
#           self._is_dhw = True
#       else:
#           self._name = data['name']
#           self._is_dhw = False

#           status=self.client.locations[0].status()
#           _LOGGER.info(status)

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
            _LOGGER.error("Update (of location) failed: TypeError (has OAuth token timed out?)")

### http://www.automatedhome.co.uk/vbulletin/showthread.php?3863-Decoded-EvoHome-API-access-to-control-remotely&p=20192&viewfull=1#post20192
# curl -s -v https://rs.alarmnet.com/TotalConnectComfort/Auth/OAuth/Token
#     -H "Authorization: Basic YjAxM2FhMjYtOTcyNC00ZGJkLTg4OTctMDQ4YjlhYWRhMjQ5OnRlc3Q="
#     --data "grant_type=refresh_token&scope=EMEA-V1-Basic+EMEA-V1-Anonymous+EMEA-V1-Get-Current-User-Account&refresh_token=<REFRESH_TOKEN>"

            self.client.access_token = None
            self.client._login()
            return

        ec_tcs = ec_tmp['gateways'][0]['temperatureControlSystems'][0]
#       _LOGGER.info(ec_tcs)

#       self.client.system_mode = ec_tcs['systemModeStatus']['mode']
        self._operating_mode = ec_tcs['systemModeStatus']['mode']
        _LOGGER.info("Current system mode (of location/controller) is: %s", self._operating_mode)

        for child in self._childZones:
            _LOGGER.debug("for child %s (%s)...", child._id, child._name)
            for zone in ec_tcs['zones']:
                _LOGGER.debug("is it zone %s (%s)?", zone['zoneId'], zone['name'])
                if zone['zoneId'] == child._id:
                    child._current_temperature = zone['temperatureStatus']['temperature']
                    child._target_temperature = zone['heatSetpointStatus']['targetTemperature']
                    child._operating_mode = zone['heatSetpointStatus']['setpointMode']

                    _LOGGER.info("Zone: %s, Temp: %s, Setpoint %s, Mode: %s", child._name, child._current_temperature, child._target_temperature, child._operating_mode)
#                   child.update()
                    break



class evoZone(ClimateDevice):
    """Representation of a Honeywell evohome zone (thermostat)."""

    def __init__(self, client, zone):
        """Initialize the zone."""
        _LOGGER.info("Creating zone  (__init__): id %s, name: %s", zone['zoneId'], zone['name'])

        self.client = client
        self._id = zone['zoneId']
        self._name = zone['name']

        self._supported_features = (SUPPORT_TARGET_TEMPERATURE | SUPPORT_OPERATION_MODE)
# do it explicitly to have a 'nice' order 
#       self._operation_list = zone['heatSetpointCapabilities']['allowedSetpointModes']
        self._operation_list = [ "FollowSchedule", "TemporaryOverride", "PermanentOverride"]

        self._operating_mode = None

        self._min_temp = zone['heatSetpointCapabilities']['minHeatSetpoint']
        self._max_temp = zone['heatSetpointCapabilities']['maxHeatSetpoint']
        self._temperature_unit = TEMP_CELSIUS

        self._current_temperature = None
        self._target_temperature = None

#       self._master = master
#       self._is_dhw = False
#       self._away_temp = 10
#       self._away = False


    @property
    def icon(self):
        """Return the icon to use in the frontend UI, if any."""
        return "mdi:radiator"  ## is usually: mdi:nest-thermostat

    @property
    def name(self):
        """Get the name of the zone."""
        _LOGGER.info("Just started: name(%s)", self._name)
        return self._name

    @property
    def supported_features(self):
        """Get the list of supported features of the zone."""
        _LOGGER.info("Just started: supported_features(%s)", self._name)
#       supported = (SUPPORT_TARGET_TEMPERATURE)
#       if hasattr(self.client, ATTR_SYSTEM_MODE):
#           supported |= SUPPORT_OPERATION_MODE
        return self._supported_features

    @property
    def temperature_unit(self):
        """Get the unit of measurement of the zone."""
        _LOGGER.debug("Just started: temperature_unit(%s)", self._name)
        return self._temperature_unit

    @property
    def precision(self):
        """Get the precision of the zone."""
        return PRECISION_HALVES

    @property
    def current_temperature(self):
        """Get the current temperature of the zone."""
        _LOGGER.info("Just started: current_temperature(%s)", self._name)
        return self._current_temperature

    def set_temperature(self, **kwargs):
        """Set a target temperature (setpoint) for the zone."""
        _LOGGER.info("Just started: set_temperature(%s)", self._name)

        temperature = kwargs.get(ATTR_TEMPERATURE)
        if temperature is None:
            return
        zone = self.client.locations[0]._gateways[0]._control_systems[0].zones[self._name]
        zone.set_temperature(temperature)
        self._operating_mode = "PermanentOverride"

    @property
    def target_temperature(self):
        """Get the current target temperature (setpoint) of the zone."""
        _LOGGER.info("Just started: target_temperature(%s)", self._name)
#       if self._is_dhw:
#           return None
        return self._target_temperature

    @property
    def current_operation(self: ClimateDevice) -> str:
        """Get the current operating mode of the zone."""
        _LOGGER.info("Just started: current_operation(%s)", self._name)
#       return getattr(self.client, ATTR_SYSTEM_MODE, None)
        return self._operating_mode

    @property
    def operation_list(self):
        """Get the list of available operations fro the zone."""
        _LOGGER.info("Just started: operation_list(%s)", self._name)

#       op_list = []
#       for mode in EVOHOME_STATE_MAP:
#           op_list.append(EVOHOME_STATE_MAP.get(mode))

#       return op_list
        return self._operation_list


    def set_operation_mode(self: ClimateDevice, operation: str, setpoint=None, until=None) -> None:
        """Set the operating mode of the zone."""
        _LOGGER.info("for zone = %s: set_operation_mode(%s, %s, %s)", self._name, operation, setpoint, until)

#       zone = self.client.locations[0]._gateways[0]._control_systems[0].zones_by_id['3432521'])
        zone = self.client.locations[0]._gateways[0]._control_systems[0].zones_by_id[self._id]

        _LOGGER.info("for zone = %s (self)", self)
        _LOGGER.info("for zone = %s (zone)", zone)

        if operation == 'FollowSchedule':
            zone.cancel_temp_override(zone)

        else:
            setpoint = self._target_temperature

            if operation == 'TemporaryOverride':
                until = datetime.now() + timedelta(1/24)
                zone.set_temperature(setpoint, until)

            elif operation == 'PermanentOverride':
                zone.set_temperature(setpoint)

        _LOGGER.info("for zone, name = %s (self._name)", self._name)
#       _LOGGER.info("for zone, name = %s (zone._name)", zone._name)

        self._operating_mode = operation

#       sleep(10)
#       self.client.xx[0].update()




#   @property
#   def is_away_mode_on(self):
#       """Return true if Away mode is on."""
#       _LOGGER.info("Just started: is_away_mode_on(%s)", self._name)
#       return self._away

#   def turn_away_mode_on(self):
#       """Turn away on.
#       Honeywell does have a proprietary away mode, but it doesn't really work
#       the way it should. For example: If you set a temperature manually
#       it doesn't get overwritten when away mode is switched on.
#       """
#       _LOGGER.info("Just started: turn_away_mode_on(%s)", self._name)
#       self._away = True
#       self.client.set_status_away() # Heating and hot water off

#   def turn_away_mode_off(self):
#       """Turn away off."""
#       _LOGGER.info("Just started: turn_away_mode_off(%s)", self._name)
#       self._away = False
#       self.client.set_status_normal()

    def update(self):
        """Get the latest state (temperature, setpoint, mode) of the zone."""
        _LOGGER.info("Just started: update(%s)", self._name)

# No updates here - the controller updates all it zones
# - this use API calls, 1 per group of zones, rather than 1 each

#       child._current_temperature = zone['temp']
#       child._target_temperature = zone['setpoint']
#       child._current_operation = "unknown"


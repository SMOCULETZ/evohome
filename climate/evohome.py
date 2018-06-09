"""
Support for Honeywell (EU-only) Evohome installations: 1 controller & 1+ zones.
"""
# regarding: https://developers.home-assistant.io/docs/en/development_index.html
#  - checked with: flake8 --ignore=E303,E241 --max-line-length=150 evohome.py



import logging
import socket
from datetime import datetime, timedelta 
from time import sleep, strftime, strptime, mktime

from custom_components.evohome import (evoControllerEntity, evoZoneEntity)
#import custom_components.evohome
# ModuleNotFoundError: No module named 'homeassistant.custom_components'  (if: homeassistant.custom_components.evohome)
# ModuleNotFoundError: No module named 'homeassistant.components.evohome' (if: homeassistant.components.evohome)

import requests
import voluptuous as vol

from homeassistant.core import callback
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


from custom_components.evohome import (
    DATA_EVOHOME,
    DISPATCHER_EVOHOME,
#   DOMAIN,
    )
    

    
    
def setup_platform(hass, config, add_devices, discovery_info=None):
    """Set up a Honeywell evohome CH/DHW system (1 controller & many zones)."""

    _LOGGER.info("Started: setup_platform()")

## Pull out the domain configuration
    ec_api = hass.data[DATA_EVOHOME]['evohomeClient']
    ec_loc = hass.data[DATA_EVOHOME]['installation']
    ec_tmp = hass.data[DATA_EVOHOME]['status']

    location = ec_loc['locationInfo']
    controller = ec_loc['gateways'][0]['temperatureControlSystems'][0]
     

## Collect the (master) controller as a merge of location & controller
#   - controller ID is used here rather than location ID
    _LOGGER.info("Found Controller: id: %s, type: %s, name: %s", 
        controller['systemId'], 
        controller['modelType'], 
        location['name']
        )
        
    master = evoController(hass, ec_api, controller)  # create the controller

## Collect each (slave) zone as a (climate component) device
    evo_devices = []
    for zone in controller['zones']:
        _LOGGER.info("Found Zone: id: %s, type: %s, name: %s", 
            zone['zoneId'], 
            zone['zoneType'], 
            zone['name']
            )
            
## We don't yet handle DHW - how to exclude as a zone?
      # if zone['zoneType'] in [ "RadiatorZone", "ZoneValves" ]:  
        slave = evoZone(hass, ec_api, zone)  # create a zone
        evo_devices.append(slave)  # add this zone to the list of devices


## Add controller and all zones in one batch for efficiency
    add_devices([ master ] + evo_devices, False)

    _LOGGER.info("Finished: setup_platform()")
    return True


    

class evoController(evoControllerEntity):
#lass evoController(evoControllerEntity, ClimateDevice):
    """Representation of a Honeywell evohome controller."""

    def __init__(self, hass, client, controller):
        """Initialize the controller."""

        _LOGGER.debug("Started: __init__(controller = %s)", controller)
        super().__init__(hass, client, controller)

# and listen for update packets      
        hass.helpers.dispatcher.async_dispatcher_connect(DISPATCHER_EVOHOME, 
            self._connect)  ## def async_dispatcher_connect(signal, target):  

# Process updates in parallel?
        parallel_updates = True

        _LOGGER.debug("Finished: __init__(controller)")
#       return


    @callback
    def _connect(self, packet):
        """Process a dispatcher connect."""
        _LOGGER.debug("Just received %s packet from %s", 
            packet['signal'], packet['sender'])

#       self.update
        self.async_schedule_update_ha_state()


    def update(self):
        super().update()

# ZX: Now (wait 5s and then) send a message to the slaves to update themselves
# store data in hass.data, platforms subscribe with dispatcher_connect, component notifies of updates using dispatch_send
#       sleep(5)
#       _LOGGER.info("About to send UPDATE packet...")
#       self.hass.helpers.dispatcher.async_dispatcher_send(DISPATCHER_EVOHOME, "UPDATE")        ## def async_dispatcher_send(hass, signal, *args):
#       self.hass.helpers.dispatcher.async_dispatcher_connect(DISPATCHER_EVOHOME, self.target)  ## def async_dispatcher_connect(hass, signal, target):





class evoZone(evoZoneEntity):
    """Representation of a Honeywell evohome heating zone."""

    def __init__(self, hass, client, zone):
        """Initialize the zone."""

        _LOGGER.debug("Started: __init__(zone = %s)", zone)
        super().__init__(hass, client, zone)

# and listen for update packets      
        hass.helpers.dispatcher.async_dispatcher_connect(DISPATCHER_EVOHOME, 
            self._connect)  ## def async_dispatcher_connect(signal, target):  

        _LOGGER.debug("Finished: __init__(zone)")
#       return


    @callback
    def _connect(self, packet):
        """Process a dispatcher connect."""
        _LOGGER.debug("Just received %s packet from %s", packet['signal'], packet['sender'])

        self.update

        self.async_schedule_update_ha_state()

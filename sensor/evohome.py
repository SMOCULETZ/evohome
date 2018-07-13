"""
Support for Honeywell Evohome (EU): a controller with 0+ zones +/- DHW.
"""

from custom_components.evohome import (
#   evoTcsDevice, evoZoneDevice, 
    evoDhwSensorDevice,
#   evoDhwSwitchDevice,
    DATA_EVOHOME, CONF_LOCATION_ID,
)
import logging
_LOGGER = logging.getLogger(__name__)

def setup_platform(hass, config, add_devices, discovery_info=None):
    """Set up a Honeywell evohome CH/DHW system: this is the DHW controller."""
# NB: DHW is a sensor/switch pair as there is no BOILER entity template
    _LOGGER.info("Started: setup_platform(), SENSOR")

# Pull out the domain configuration from hass.data
    ec_api = hass.data[DATA_EVOHOME]['evohomeClient']
    ec_idx = hass.data[DATA_EVOHOME]['config'][CONF_LOCATION_ID]
    ec_loc = ec_api.installation_info[ec_idx]

# Collect the (master) controller
    tcsObjRef = ec_api.locations[ec_idx]._gateways[0]._control_systems[0]

    _LOGGER.info(
        "Controller object [idx=%s]: Found, id: %s [%s], type: %s",
        ec_idx,
        tcsObjRef.systemId,
        tcsObjRef.location.name,
        tcsObjRef.modelType
    )

    
    
    
# Collect any DHW zone as a (SENSOR component) device
    if tcsObjRef.hotwater:
        _LOGGER.info(
            "DHW SENSOR object: Found, zoneId(dhwId): %s, type: %s",
            tcsObjRef.hotwater.zoneId,
            tcsObjRef.hotwater.zone_type
        )

        slave = evoDhwSensorDevice(hass, ec_api, tcsObjRef.hotwater)
        add_devices([slave], False)

    else:
        _LOGGER.info("DHW SENSOR object: None found")

    
    
    _LOGGER.info("Finished: setup_platform(), SENSOR")
    return True
    
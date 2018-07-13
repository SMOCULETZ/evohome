"""
Support for Honeywell Evohome (EU): a controller with 0+ zones +/- DHW.
"""

from custom_components.evohome import (
    evoTcsDevice, evoZoneDevice, 
#   evoDhwSensorDevice,
#   evoDhwSwitchDevice,
    DATA_EVOHOME, CONF_LOCATION_ID,
)
import logging
_LOGGER = logging.getLogger(__name__)

def setup_platform(hass, config, add_devices, discovery_info=None):
    """Set up a Honeywell evohome CH/DHW system: 1 controller & many zones."""

    _LOGGER.info("Started: setup_platform(), CLIMATE")

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

    master = evoTcsDevice(hass, ec_api, tcsObjRef)  # create the controller
    slaves = []

# Collect each (slave) zone as a (CLIMATE component) device
    for zoneObjRef in tcsObjRef._zones:
        _LOGGER.info(
            "Found Zone object: id: %s, type: %s",
            zoneObjRef.zoneId + " [" + zoneObjRef.name + "] ",
            zoneObjRef.zoneType
        )

        slave = evoZoneDevice(hass, ec_api, zoneObjRef)
        slaves.append(slave)

    if len(slaves) == 0:
        _LOGGER.info("Zone CLIMATE objects: None found")

    add_devices([master] + slaves, False)

    _LOGGER.info("Finished: setup_platform(), CLIMATE")
    return True
    
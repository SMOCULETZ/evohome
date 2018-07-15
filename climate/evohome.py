"""
Support for Honeywell Evohome (EU): a controller with 0+ zones +/- DHW.
"""

from custom_components.evohome import (
    evoController, 
    evoZone, 
    evoDhwSensor,
    evoDhwSwitch,
    
    DATA_EVOHOME, 
    CONF_LOCATION_ID,
)

import logging

_LOGGER = logging.getLogger(__name__)


def setup_platform(hass, config, add_devices, discovery_info=None):
    """Set up a Honeywell evohome CH/DHW system."""

    _LOGGER.info("Started: setup_platform()")

# Pull out the domain configuration from hass.data
    ec_api = hass.data[DATA_EVOHOME]['evohomeClient']
    ec_idx = hass.data[DATA_EVOHOME]['config'][CONF_LOCATION_ID]
    ec_loc = ec_api.installation_info[ec_idx]


# Collect the (master) controller
    tcsObjRef = ec_api.locations[ec_idx]._gateways[0]._control_systems[0]

    _LOGGER.info(
        "Found Controller object [idx=%s]: id: %s [%s], type: %s",
        ec_idx,
        tcsObjRef.systemId,
        tcsObjRef.location.name,
        tcsObjRef.modelType
    )

    master = evoController(hass, ec_api, tcsObjRef)  # create the controller
    slaves = []


# Collect each (slave) zone as a (climate component) device
    for zoneObjRef in tcsObjRef._zones:
        _LOGGER.info(
            "Found Zone object: id: %s, type: %s",
            zoneObjRef.zoneId + " [" + zoneObjRef.name + " ]",
            zoneObjRef.zoneType
        )

# We may not handle some zones correctly (e.g. UFH) - how to test for them?
#       if zone['zoneType'] in [ "RadiatorZone", "ZoneValves" ]:
        slave = evoZone(hass, ec_api, zoneObjRef)  # create a zone (new way, as object)
        slaves.append(slave)  # add this zone to the list of devices


# 3/3: Collect any (slave) DHW zone as a (climate component) device
    if tcsObjRef.hotwater:
        _LOGGER.info(
            "Found DHW object: dhwId: %s, zoneId: %s, type: %s",
            tcsObjRef.hotwater.dhwId,
            tcsObjRef.hotwater.zoneId,
            tcsObjRef.hotwater.zone_type
        )
        
        slave = evoDhwSensor(hass, ec_api, tcsObjRef.hotwater)  # create a DHW zone
        slaves.append(slave)  # add this DHW zone to the list of devices

        slave = evoDhwSwitch(hass, ec_api, tcsObjRef.hotwater)  # create a DHW zone
        slaves.append(slave)  # add this DHW zone to the list of devices

        
# Now, for efficiency) add controller and all zones in a single call
    add_devices([master] + slaves, False)

    _LOGGER.info("Finished: setup_platform()")
    return True

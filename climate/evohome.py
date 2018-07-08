"""
Support for Honeywell (EU-only) Evohome installations: 1 controller & 1+ zones.
"""

import logging

from custom_components.evohome import (
    evoControllerEntity,
    evoZoneEntity,
    evoDhwEntity,

    DATA_EVOHOME,
)

_LOGGER = logging.getLogger(__name__)


def setup_platform(hass, config, add_devices, discovery_info=None):
    """Set up a Honeywell evohome CH/DHW system (1 controller & many zones)."""

    _LOGGER.info("Started: setup_platform()")

# Pull out the domain configuration from hass.data
    ec_api = hass.data[DATA_EVOHOME]['evohomeClient']
    ec_loc = hass.data[DATA_EVOHOME]['installation']
#   ec_tmp = hass.data[DATA_EVOHOME]['status']  # not needed during init


# 1/3: Collect the (master) controller (a merge of location & controller)
#  - controller ID is used in preference to location ID
    tcsObjRef = ec_api.locations[0]._gateways[0]._control_systems[0]

    _LOGGER.info(
        "Found Controller object (0:0:0): id: %s [%s], type: %s",
        tcsObjRef.systemId,
        tcsObjRef.location.name,
        tcsObjRef.modelType
    )

# 1/3: Collect the (master) controller (a merge of location & controller)
#  - controller ID is used in preference to location ID
    location = ec_loc['locationInfo']
    controller = ec_loc['gateways'][0]['temperatureControlSystems'][0]

    _LOGGER.info(
        "(OLD:) Found Controller: id: %s [%s], type: %s",
        controller['systemId'],
        location['name'],
        controller['modelType']
    )

    master = evoController(hass, ec_api, controller)  # create the controller
    slaves = []


# 2/3: Collect each (slave) zone as a (climate component) device
    for zoneObjRef in tcsObjRef._zones:
        _LOGGER.info(
            "Found Zone object: id: %s [%s], type: %s",
            zoneObjRef.zoneId,
            zoneObjRef.name,
            zoneObjRef.zoneType
#           zoneObjRef.zone_type
        )

        slave = evoZone(hass, ec_api, zoneObjRef)  # create a zone (new way, as object)
        slaves.append(slave)  # add this zone to the list of devices

# 2/3: Collect each (slave) zone as a (climate component) device
    for zone in controller['zones']:
        _LOGGER.info(
            "(OLD:) Found Zone: id: %s [%s], type: %s",
            zone['zoneId'],
            zone['name'],
            zone['zoneType']
        )

# We may not handle some zones correctly (e.g. UFH) - how to test for them?
#       if zone['zoneType'] in [ "RadiatorZone", "ZoneValves" ]:
#       slave = evoZone(hass, ec_api, zone)  # create a zone (old way)
#       slaves.append(slave)  # add this zone to the list of devices


# 3/3: Collect any (slave) DHW zone as a (climate component) device
    if 'dhw' in controller:
        _LOGGER.info(
            "(OLD:) Found DHW: id: %s",
            controller['dhw']['dhwId']
        )

# 3/3: Collect any (slave) DHW zone as a (climate component) device
    if tcsObjRef.hotwater:
        _LOGGER.info(
            "Found DHW object: dhwId: %s, zoneId: %s, type: %s",
            tcsObjRef.hotwater.dhwId,
            tcsObjRef.hotwater.zoneId,
            tcsObjRef.hotwater.zone_type
        )
        
        slave = evoDhw(hass, ec_api, tcsObjRef.hotwater)  # create a DHW zone
        slaves.append(slave)  # add this DHW zone to the list of devices


# Now, for efficiency) add controller and all zones in a single call
    add_devices([master] + slaves, False)

    _LOGGER.info("Finished: setup_platform()")
    return True


class evoController(evoControllerEntity):
    """Representation of a Honeywell evohome Controller (hub)."""


class evoZone(evoZoneEntity):
    """Representation of a Honeywell evohome Heating zone."""


class evoDhw(evoDhwEntity):
    """Representation of a Honeywell evohome DHW zone."""

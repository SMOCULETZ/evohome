"""
Support for Honeywell (EU-only) Evohome installations: 1 controller & 1+ zones.
"""

import logging

from homeassistant.core import callback  # used for @callback

from custom_components.evohome import (
    evoControllerEntity,
    evoZoneEntity,
    evoDhwEntity,

    DATA_EVOHOME,
    DISPATCHER_EVOHOME,
)

_LOGGER = logging.getLogger(__name__)


def setup_platform(hass, config, add_devices, discovery_info=None):
    """Set up a Honeywell evohome CH/DHW system (1 controller & many zones)."""

    _LOGGER.info("Started: setup_platform()")


# Pull out the domain configuration fromm hass.data
    ec_api = hass.data[DATA_EVOHOME]['evohomeClient']
    ec_loc = hass.data[DATA_EVOHOME]['installation']
#   ec_tmp = hass.data[DATA_EVOHOME]['status']  # not needed during init

    location = ec_loc['locationInfo']
    controller = ec_loc['gateways'][0]['temperatureControlSystems'][0]


# Collect the (master) controller (a merge of location & controller)
#  - controller ID is used in preference to location ID
    _LOGGER.info(
        "Found Controller: id: %s [%s], type: %s",
        controller['systemId'],
        location['name'],
        controller['modelType']
    )

    master = evoController(hass, ec_api, controller)  # create the controller

    
    slaves = []
# Collect each (slave) zone as a (climate component) device
    for zone in controller['zones']:
        _LOGGER.info(
            "Found Zone: id: %s [%s], type: %s",
            zone['zoneId'],
            zone['name'],
            zone['zoneType']
        )

        
# We may not handle some zones correctly (e.g. UFH) - how to test for them?
#       if zone['zoneType'] in [ "RadiatorZone", "ZoneValves" ]:
        if True:
            slave = evoZone(hass, ec_api, zone)  # create a zone
            slaves.append(slave)  # add this zone to the list of devices


# Collect any (slave) DHW zone as a (climate component) device
    if 'dhw' in controller:
#   if 'modelType' in controller:
        _LOGGER.info(
            "Found DHW: id: %s",
            controller['dhw']['dhwId']
        )

        slave = evoDhw(hass, ec_api, controller['dhw'])  # create a zone
        slaves.append(slave)  # add this DHW zone to the list of devices

# Now, add controller and all zones in one batch for efficiency
    add_devices([master] + slaves, False)

    _LOGGER.info("Finished: setup_platform()")
    return True


class evoController(evoControllerEntity):
    """Representation of a Honeywell evohome hub/controller."""
        

class evoZone(evoZoneEntity):
    """Representation of a Honeywell evohome heating zone."""

class evoDhw(evoDhwEntity):
    """Representation of a Honeywell evohome DHW controller."""


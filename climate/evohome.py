"""
Support for Honeywell (EU-only) Evohome installations: 1 controller & 1+ zones.
"""

import logging

from homeassistant.core import callback  # used for @callback

from custom_components.evohome import (
    evoControllerEntity,
    evoZoneEntity,

    DATA_EVOHOME,
    DISPATCHER_EVOHOME,
)

# REQUIREMENTS = ['evohomeclient==0.2.5']

_LOGGER = logging.getLogger(__name__)


def setup_platform(hass, config, add_devices, discovery_info=None):
    """Set up a Honeywell evohome CH/DHW system (1 controller & many zones)."""

    _LOGGER.info("Started: setup_platform()")


# Pull out the domain configuration fromm hass.data
    ec_api = hass.data[DATA_EVOHOME]['evohomeClient']
    ec_loc = hass.data[DATA_EVOHOME]['installation']
#   ec_tmp = hass.data[DATA_EVOHOME]['status']

    location = ec_loc['locationInfo']
    controller = ec_loc['gateways'][0]['temperatureControlSystems'][0]


# Collect the (master) controller (a merge of location & controller)
#  - controller ID is used in preference to location ID
    _LOGGER.info(
        "Found Controller: id: %s, type: %s, name: %s",
        controller['systemId'],
        controller['modelType'],
        location['name']
    )

    master = evoController(hass, ec_api, controller)  # create the controller

# Collect each (slave) zone as a (climate component) device
    evo_devices = []
    for zone in controller['zones']:
        _LOGGER.info(
            "Found Zone: id: %s, type: %s, name: %s",
            zone['zoneId'],
            zone['zoneType'],
            zone['name']
        )

# We don't yet handle DHW - how to exclude as a zone?
#     # if zone['zoneType'] in [ "RadiatorZone", "ZoneValves" ]:
        slave = evoZone(hass, ec_api, zone)  # create a zone
        evo_devices.append(slave)  # add this zone to the list of devices

# Add controller and all zones in one batch for efficiency
    add_devices([master] + evo_devices, False)

    _LOGGER.info("Finished: setup_platform()")
    return True


class evoController(evoControllerEntity):
    """Representation of a Honeywell evohome controller."""

    def __init__(self, hass, client, controller):
        """Initialize the evohome controller."""

        _LOGGER.debug("Started: __init__(controller = %s)", controller)
        super().__init__(hass, client, controller)

# listen for update packets...
        hass.helpers.dispatcher.async_dispatcher_connect(
            DISPATCHER_EVOHOME,
            self._connect
        )  # for: def async_dispatcher_connect(signal, target)

# Process updates in parallel???
#       parallel_updates = True

        _LOGGER.debug("Finished: __init__(controller)")
        return

    @callback
    def _connect(self, packet):
        """Process a dispatcher connect."""
        _LOGGER.debug(
            "Just received a '%s' packet from %s",
            packet['signal'],
            packet['sender']
        )

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
        """Initialize the evohome zone."""

        _LOGGER.debug("Started: __init__(zone = %s)", zone)
        super().__init__(hass, client, zone)

# listen for update packets...
        hass.helpers.dispatcher.async_dispatcher_connect(
            DISPATCHER_EVOHOME,
            self._connect
        )  # for: def async_dispatcher_connect(signal, target)

        _LOGGER.debug("Finished: __init__(zone)")
        return

    @callback
    def _connect(self, packet):
        """Process a dispatcher connect."""
        _LOGGER.debug(
            "Just received a '%s' packet from %s",
            packet['signal'],
            packet['sender']
        )

        self.update
        self.async_schedule_update_ha_state()

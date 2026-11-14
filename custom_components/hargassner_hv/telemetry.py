# SPDX-License-Identifier: Apache-2.0
"""Reviewed operational channel meanings; unknown channels remain raw diagnostics.

Units come from firmware channel/sensor definitions, not name heuristics.
See docs/channel-guide.md for evidence and validity limitations.
"""

from dataclasses import dataclass
from types import MappingProxyType

from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass
from homeassistant.const import PERCENTAGE, REVOLUTIONS_PER_MINUTE, UnitOfTemperature


@dataclass(frozen=True)
class OperationalChannel:
    key: str
    name: str
    unit: str
    device_class: SensorDeviceClass | None = None
    state_class: SensorStateClass | None = None
    circuit: str | None = None
    unavailable_values: tuple[float, ...] = ()
    default_active_parameter: str | None = None


def _temperature(key: str, name: str, *, measured: bool = True,
                 circuit: str | None = None, room: bool = False,
                 parameter: str | None = None) -> OperationalChannel:
    return OperationalChannel(
        key, name, UnitOfTemperature.CELSIUS, SensorDeviceClass.TEMPERATURE,
        SensorStateClass.MEASUREMENT if measured and not room else None,
        circuit, (-1.0,) if room else (), parameter,
    )


OPERATIONAL_CHANNELS = MappingProxyType({description.key: description for description in (
    _temperature("kesselfuehler", "Boiler water temperature"),
    _temperature("ruecklauffuehler", "Boiler return temperature"),
    _temperature("rauchgasfuehler_nicrni", "Flue gas temperature"),
    _temperature("aussentemperatur", "Outdoor temperature"),
    _temperature("AussentempMittel", "Outdoor temperature (controller average)"),
    _temperature("puffer_oben", "Buffer top temperature"),
    _temperature("puffer_mitte", "Buffer middle temperature"),
    _temperature("puffer_unten", "Buffer bottom temperature"),
    _temperature("HkmHk1Fuehler", "Circuit 1 flow temperature", circuit="1"),
    _temperature("HkmHk2Fuehler", "Circuit 2 flow temperature", circuit="2"),
    _temperature("HkaHkFuehler", "Circuit A flow temperature", circuit="A"),
    _temperature("HkmBoilerFuehler", "DHW cylinder 1 temperature", parameter="2"),
    _temperature("HkaBoilerFuehler", "DHW cylinder A temperature", parameter="HP2"),
    _temperature("Hk1Fr35RaumTemp", "Circuit 1 room temperature (reported)", circuit="1", room=True),
    _temperature("Hk2Fr35RaumTemp", "Circuit 2 room temperature (reported)", circuit="2", room=True),
    _temperature("HkAFr35RaumTemp", "Circuit A room temperature (reported)", circuit="A", room=True),
    _temperature("TKsoll", "Boiler water calculated target", measured=False),
    _temperature("RL_Soll", "Boiler return calculated target", measured=False),
    _temperature("Hk1Soll", "Circuit 1 calculated flow target", measured=False, circuit="1"),
    _temperature("Hk2Soll", "Circuit 2 calculated flow target", measured=False, circuit="2"),
    _temperature("HkASoll", "Circuit A calculated flow target", measured=False, circuit="A"),
    _temperature("HeizkreisAnforderung", "Heating circuits requested temperature", measured=False),
    _temperature("BoilerAnforderung", "DHW requested temperature", measured=False),
    OperationalChannel("Pufferfuellgrad", "Buffer charge indication", PERCENTAGE),
    OperationalChannel("Leistung", "Boiler output indication", PERCENTAGE),
    OperationalChannel("lambda_O2", "Flue gas oxygen", PERCENTAGE, state_class=SensorStateClass.MEASUREMENT),
    OperationalChannel("O2Soll", "Flue gas oxygen target", PERCENTAGE),
    OperationalChannel("saugzug_rpm", "Induced-draught fan speed", REVOLUTIONS_PER_MINUTE,
                       state_class=SensorStateClass.MEASUREMENT),
    OperationalChannel("SzSollRPM", "Induced-draught fan speed target", REVOLUTIONS_PER_MINUTE),
)})

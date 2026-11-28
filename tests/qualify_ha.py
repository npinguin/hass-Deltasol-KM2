"""Real HA lifecycle test with an independent loopback VBus protocol peer.

The peer models all ten local and registered EM relay capabilities.
It is an independent protocol test, not physical RESOL evidence.
"""

import ast
import asyncio
import json
import shutil
import socketserver
import struct
import sys
import threading
from contextlib import ExitStack, nullcontext
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import MappingProxyType
from typing import ClassVar
from unittest.mock import patch

ADDRESS = 0x7112
RELAY = 2019
CHANGESET = 0x2734DABC
SENSOR_UID = "00_0010_7112_0100__000_2_0"
SELECT_UID = "isolated-gateway_7112_R4_mode"
EM_UID = "isolated-gateway_7112_M1_R1_mode"
EM_RELAY = 2173
EM_SENSOR_UID = "00_7112_6651_0100__000_4_0"
HQM_SENSOR_UID = "00_0010_7131_0100__004_4_0"

# Independent wire fixtures: exact 2.08 reference positions, not imported
# production Setting objects. Persistent state belongs to the protocol peer.
CONFIG_SELECTORS = {
    "Heizung_Heizkreis1_OSchaltuhr": (13703, 0),
    "Heizung_Heizkreis1_Aktiviert_SensorSchalter_Nr": (13691, 4),
    "Ausgang_Regler_A1_AktorFunktion": (2602, 7),
    "Ausgang_Regler_A1_Handbetrieb": (2612, 2),
    "Ausgang_Regler_A1_ODrehzahl": (2606, 0),
    "Heizung_Wf2_SolarAus_SpeicherMaske": (11434, 1),
    "Heizung_Heizkreis1_Nachheizung_SolarAus_SpeicherMaske": (14142, 1),
    "Solar_Wf8_Type": (5249, 6),
    "Solar_Wf8_Aktiviert_Modus": (5255, 1),
    "Solar_Wf8_Relais1_Nr": (5276, 1),
    "Solar_Wf8_NhUnterdrueckung_SpeicherMaske": (5301, 1),
    "Anlage_Wf8_Type": (8316, 3),
    "Anlage_Wf8_Aktiviert_Modus": (8323, 1),
    "Anlage_Wf8_Relais1_Nr": (8349, 1),
    "Anlage_Wf8_Sensor1_Nr": (8333, 2),
    "Anlage_Wf8_Sensor2_Nr": (8337, 3),
    "Heizung_Wf3_BwVorerwaermung_OVentil": (11690, 0),
    "Heizung_Wf3_Relais1_Nr": (11623, 4),
    "Heizung_Wf3_SensorDurchfluss1_Nr": (11617, 4),
    "Heizung_Wf3_Sensor3_Nr": (11600, 3),
    "Heizung_Wf3_Sensor2_Nr": (11596, 2),
    "Heizung_Wf3_Sensor1_Nr": (11592, 1),
    "Heizung_Wf3_Aktiviert_Modus": (11575, 1),
    "Heizung_Wf3_Type": (11568, 3),
    "Anlage_Wf2_Relais1_Nr": (6843, 2),
    "Anlage_Wf2_Sensor3_Nr": (6835, 3),
    "Anlage_Wf2_Sensor2_Nr": (6831, 2),
    "Anlage_Wf2_Sensor1_Nr": (6827, 1),
    "Anlage_Wf5_Sensor4_Nr": (7592, 4),
    "Anlage_Wf5_Sensor3_Nr": (7588, 3),
    "Anlage_Wf5_Sensor2_Nr": (7584, 2),
    "Anlage_Wf5_Sensor1_Nr": (7580, 1),
    "Anlage_Wf7_Type": (8065, 8),
    "Anlage_Wf7_Aktiviert_Modus": (8072, 1),
    "Anlage_Wf7_Relais1_Nr": (8098, 2),
    "Anlage_Wf7_Sensor1_Nr": (8082, 1),
    "Anlage_Wf7_Sensor2_Nr": (8086, 2),
    "Anlage_Wf7_Sensor3_Nr": (8090, 3),
    "Heizung_Heizkreis1_Energiesparbetrieb_Sensor1_Nr": (14160, 5),
    "Heizung_Wf2_Type": (11417, 2),
    "Heizung_Wf2_Aktiviert_Modus": (11424, 1),
    "Heizung_Wf2_ThBwErwaermung_OLadepumpe": (11526, 1),
    "Heizung_Wf2_Relais2_Nr": (11480, 2),
    "Heizung_Wf2_NhRelais1_Nr": (11488, 3),
    "Heizung_Wf2_Sensor1_Nr": (11441, 4),
    "Heizung_Wf2_Sensor2_Nr": (11445, 5),
    "Heizung_Wf2_SensorBetriebsartenschalter1_Nr": (11458, 6),
    "Heizung_Wf2_SensorSchalter1_Nr": (11462, 7),
    "SDKarte_Status_KarteVorhanden": (18227, 1),
    "SDKarte_Status_Schreibfehler": (18228, 0),
    "SDKarte_Status_KarteUnbekannt": (18229, 0),
    "SDKarte_Status_KarteUnformatiert": (18230, 0),
    "SDKarte_Status_KarteVoll": (18231, 0),
    "SDKarte_Status_KarteSchreibgeschuetzt": (18232, 0),
    "Anlage_Wf1_Sensor1_Nr": (6576, 1),
    "Anlage_Wf1_Relais1_Nr": (6592, 2),
    "Anlage_Wf1_Relais2_Nr": (6600, 3),
    "Heizung_Heizkreis1_Kuehlung_ExtSchalter_Sensor1_Nr": (14068, 8),
    "Heizung_Heizkreis1_Kuehlung_Kuehlsystem": (14075, 0),
    "Anlage_Wf1_Type": (6559, 2),
    "Anlage_Wf1_Aktiviert_SensorSchalter_Nr": (6568, 4),
    "Anlage_Wf2_Type": (6810, 6),
    "Anlage_Wf2_Nr": (6811, 1),
    "Heizung_Wf2_FeststoffkesselMaske": (11437, 1),
    "Heizung_Heizkreis1_Nachheizung_FeststoffkesselMaske": (14038, 1),
    "Anlage_Wf10_Type": (8818, 0),
    "Anlage_Wf11_Type": (9069, 0),
    "Anlage_Wf12_Type": (9320, 0),
    "Anlage_Wf13_Type": (9571, 0),
    "Anlage_Wf14_Type": (9822, 0),
    "Anlage_Wf15_Type": (10073, 0),
    "Anlage_Wf16_Type": (10324, 0),
    "Anlage_Wf2_Aktiviert_Modus": (6817, 1),
    "Anlage_Wf3_Type": (7061, 11),
    "Anlage_Wf3_Aktiviert_Modus": (7068, 1),
    "Anlage_Wf3_Sensor1_Nr": (7078, 1),
    "Anlage_Wf3_Sensor2_Nr": (7082, 2),
    "Anlage_Wf3_Sensor3_Nr": (7086, 3),
    "Anlage_Wf3_Relais1_Nr": (7094, 1),
    "Anlage_Wf3_Relais2_Nr": (7102, 2),
    "Heizung_Heizkreis1_Type": (13684, 1),
    "Heizung_Heizkreis1_Heizsystem": (13978, 1),
    "Heizung_Heizkreis1_Sensor1_Nr": (13796, 1),
    "Heizung_Heizkreis1_Relais1_Nr": (13764, 1),
    "Heizung_Heizkreis1_Relais2_Nr": (13772, 2),
    "Heizung_Heizkreis1_Relais3_Nr": (13780, 3),
    "Heizung_RelaisAnforderung1_Type": (10724, 1),
    "Heizung_RelaisAnforderung1_OAusgang1": (10744, 1),
    "Heizung_RelaisAnforderung1_Ausgang1_Nr": (10745, 1),
    "Heizung_RelaisAnforderung1_Ausgang_OVorlauf": (10773, 1),
    "Heizung_RelaisAnforderung1_Ausgang_Vorlauf_Sensor1_Nr": (10775, 2),
    "Heizung_RelaisAnforderung1_Aktiviert_Modus": (10727, 1),
    "Heizung_RelaisAnforderung1_ORelais1": (10734, 1),
    "Heizung_RelaisAnforderung1_Relais1_Nr": (10735, 2),
    "Anlage_Wf6_Type": (7814, 5),
    "Anlage_Wf6_Aktiviert_Modus": (7821, 1),
    "Anlage_Wf6_Sensor1_Nr": (7831, 1),
    "Anlage_Wf6_Sensor2_Nr": (7835, 2),
    "Anlage_Wf6_Relais1_Nr": (7847, 4),
    "Anlage_Wf5_Type": (7563, 9),
    "Anlage_Wf5_Aktiviert_Modus": (7570, 1),
    "Anlage_Wf5_Relais1_Nr": (7596, 4),
    "Anlage_Wf5_Relais1_Funktion": (7597, 1308),
    "Aktor4_Funktion": (2675, 1308),
    "Aktor4_RelaisNr": (2676, 3),
    "Aktor4_AusgangNr": (2677, 0),
    "Relais_Regler_R3_AktorFunktion": (1999, 1308),
    "Relais_Regler_R1_AktorFunktion": (1971, 2),
    "Anlage_Wf5_Funktionsblock_Bezugsrelais1_Nr": (7741, 257),
    "Anlage_Wf5_Funktionsblock_Bezugsrelais2_Nr": (7742, 0),
    "Anlage_Wf5_Funktionsblock_Bezugsrelais3_Nr": (7743, 0),
    "Anlage_Wf5_Funktionsblock_Bezugsrelais4_Nr": (7744, 0),
    "Anlage_Wf5_Funktionsblock_Bezugsrelais5_Nr": (7745, 0),
    "Anlage_Wf9_Type": (8567, 1),
    "Anlage_Wf9_Aktiviert_SensorSchalter_Nr": (8576, 4),
    "Anlage_Wf9_Relais1_Nr": (8600, 71),
    "Anlage_Wf9_Relais1_Funktion": (8601, 2323),
    "Anlage_Wf9_Bezugsrelais1_Nr": (8624, 257),
    "Aktor71_Funktion": (3010, 2323),
    "Aktor71_RelaisNr": (3011, 2),
    "Aktor71_AusgangNr": (3012, 0),
    "Relais_Regler_R2_AktorFunktion": (1985, 2323),
    "Anlage_Wf9_Parallelrelais_dtNachlauf": (8630, 10),
    "Anlage_Wf9_Parallelrelais_dtVerzoegerung": (8632, 10),
    "Heizung_Heizkreis1_System": (13697, 2),
    "Heizung_Heizkreis1_ONachheizung": (13705, 1),
    "Heizung_Heizkreis1_OFernzugriff": (14044, 1),
    "Heizung_Heizkreis1_Fernzugriff_Modus": (14045, 2),
    "Heizung_Heizkreis1_Nachheizung_Aktiviert_Modus": (13708, 1),
    "Heizung_Heizkreis1_Sensor4_Nr": (13804, 4),
    "Heizung_Heizkreis1_Sensor5_Nr": (13808, 5),
    "Heizung_Heizkreis1_Relais6_Nr": (14003, 2),
    "Heizung_Heizkreis1_Kuehlung_Relais1_Nr": (14059, 3),
    "Anlage_WTUmwaelzpumpe_Aktiviert_Modus": (10607, 1),
    "Anlage_WTUmwaelzpumpe_Relais_Nr": (10614, 2),
    "Heizung_RelaisPumpe1_Type": (11166, 2),
    "Heizung_RelaisPumpe1_Aktiviert_Modus": (11169, 1),
    "Heizung_RelaisPumpe1_Relais1_Nr": (11171, 4),
    "Heizung_RelaisPumpe1_AnlaufNachlauf_Sensor1_Nr": (11189, 6),
    "Heizung_Heizkreis1_Sensor2_Nr": (13800, 3),
    "Heizung_Heizkreis1_Raumthermostat1_Option": (13819, 1),
    "Heizung_Heizkreis1_Raumthermostat1_OSchaltuhr": (13821, 1),
    "Heizung_Heizkreis1_Raumthermostat1_Typ": (13823, 0),
    "Heizung_Heizkreis1_Raumthermostat1_Sensor1_Nr": (13834, 4),
    "Heizung_Heizkreis2_Type": (14179, 1),
    "Heizung_Heizkreis2_Kopplung": (14534, 1),
    "InbetriebnahmeAbgeschlossen": (68, 1),
    "Wmz1_Type": (17260, 1),
    "Wmz1_Aktiviert_Modus": (17263, 1),
    "Relais_Modul1_R1_AktorFunktion": (2167, 2),
    "Solar_SystemId": (3416, 20201),
    "Solar_ParallelbeladungMoeglich": (3439, 1),
    "Solar_Wf1_Roehrenkollektor_KollektorMaske": (3839, 3),
    "Solar_Wf1_Roehrenkollektor_Aktiviert_SensorSchalter_Nr": (3836, 4),
    "Solar_Wf1_Bypass_Aktiviert_SensorSchalter_Nr": (3890, 4),
    "Solar_Wf1_CsBypass_Aktiviert_SensorSchalter_Nr": (3983, 4),
    "Solar_Wf1_Zieltemperatur_Aktiviert_SensorSchalter_Nr": (4337, 4),
    "Solar_Wf1_ExtWT_SpeicherMaske": (4089, 3),
    "Solar_Wf1_ExtWT_Sensor1_Nr": (4094, 4),
    "Solar_Wf1_ExtWT_Sensor2_Nr": (4098, 5),
    "Solar_Wf1_ExtWT_Relais1_Nr": (4102, 3),
    "Solar_Relais1_Nr": (3452, 1),
    "Solar_Relais1_Funktion": (3453, 2),
    "Aktor1_Funktion": (2660, 2),
    "Aktor1_RelaisNr": (2661, 1),
    "Aktor1_AusgangNr": (2662, 0),
    "Solar_Kol1_Sensor1_Nr": (3536, 1),
    "Solar_Sp1_Sensor1_Nr": (3576, 2),
    "Solar_Sp2_Sensor1_Nr": (3612, 3),
    "Solar_Wf1_Bypass_Sensor1_Nr": (3896, 4),
    "Solar_Wf1_Bypass_Relais1_Nr": (3900, 2),
    "Solar_Wf1_Bypass_Variante": (3894, 0),
    "Solar_Wf1_Bypass_KollektorMaske": (3893, 3),
    "Solar_Kol2_Sensor1_Nr": (3550, 5),
    "Solar_Wf1_CsBypass_SensorEinstrahlung_Nr": (3988, 6),
    "Solar_Wf1_CsBypass_KollektorMaske": (3986, 1),
    "Solar_Wf1_Zieltemperatur_Sensor1_Nr": (4341, 7),
    "Heizung_Wf1_Type": (11266, 4),
    "Heizung_Wf1_Zirkulation_Variante": (11405, 0),
    "Heizung_Wf1_Aktiviert_SensorSchalter_Nr": (11275, 4),
    "Heizung_Wf1_Sensor1_Nr": (11290, 1),
    "Heizung_Wf1_SensorSchalter1_Nr": (11311, 3),
    "Heizung_Wf1_Relais1_Nr": (11321, 2),
    "Heizung_Wf1_Zirkulation_dtLaufzeit": (11406, 180),
    "Heizung_Wf1_Zirkulation_dtPausenzeit": (11407, 30),
    "Heizung_Wf1_Zirkulation_dtVerzoegerung": (11414, 0),
}
CONFIG_SETTINGS = {
    "Heizung_Heizkreis2_Aktiviert_Modus": (14184, 1, 0, 2),
    "Heizung_Heizkreis1_Aktiviert_Modus": (13689, 1, 0, 2),
    "Solar_Beladungstyp": (3417, 0, 0, 1),
    "Solar_Losreissimpuls": (3438, 10, 10, 300),
    "Solar_Sp2_Aktiviert_Modus": (3640, 1, 0, 2),
    "Solar_Sp1_Aktiviert_Modus": (3604, 1, 0, 2),
    "Heizung_Wf2_OFeststoffkesselAus": (11436, 0, 0, 1),
    "Heizung_Heizkreis1_Nachheizung_OFeststoffkesselAus": (14037, 0, 0, 1),
    "Anlage_Wf5_Funktionsblock_OBezugsrelais": (7739, 0, 0, 1),
    "Anlage_Wf5_Funktionsblock_Bezugsrelais_Modus": (7740, 0, 0, 3),
    "Anlage_Wf9_Parallelrelais_ONachlauf": (8629, 0, 0, 1),
    "Anlage_Wf9_Parallelrelais_OVerzoegerung": (8631, 0, 0, 1),
    "Solar_Wf1_Zieltemperatur_Aktiviert_Modus": (4335, 1, 0, 2),
    "Solar_Wf1_CsBypass_Aktiviert_Modus": (3981, 1, 0, 2),
    "Solar_Wf1_Bypass_Aktiviert_Modus": (3888, 1, 0, 2),
    "Solar_Wf1_Roehrenkollektor_Aktiviert_Modus": (3834, 1, 0, 2),
    "Anlage_Wf5_Funktionsblock_OMindestausschaltzeit": (7747, 1, 0, 1),
    "Anlage_Wf1_Aktiviert_Modus": (6566, 1, 0, 2),
    "Anlage_Wf9_Aktiviert_Modus": (8574, 1, 0, 2),
    "Heizung_RelaisAnforderung1_OMindestlaufzeit": (10798, 1, 0, 1),
    "Heizung_Wf1_Aktiviert_Modus": (11273, 1, 0, 2),
    "Heizung_Heizkreis1_Nachheizung_Modus": (13706, 0, 0, 2),
    "Heizung_RelaisPumpe1_Anlauf_Modus": (11178, 2, 0, 2),
    "Heizung_RelaisPumpe1_Nachlauf_Modus": (11183, 1, 0, 2),
    "Heizung_Heizkreis1_Raumthermostat1_Aktiviert": (13820, 1, 0, 2),
    "Heizung_Heizkreis1_RTH_HKaus": (14041, 0, 0, 1),
    "Heizung_Heizkreis1_Raumthermostat1_TRaum": (13824, 200, 100, 300),
    "Heizung_Heizkreis1_Raumthermostat1_dTRaum": (13827, 10, 5, 200),
    "Heizung_RelaisPumpe1_Anlauf_TAnlauf": (11179, 400, 100, 900),
    "Heizung_Wf3_BwVorerwaermung_TMax": (11691, 600, 200, 900),
    "Heizung_Wf3_BwVorerwaermung_dTMax": (11696, 50, 5, 100),
    "Heizung_Wf2_ThBwErwaermung_Modus": (11529, 0, 0, 1),
    "Heizung_Wf2_OBetriebsartenschalterAus": (11438, 0, 0, 1),
    "Heizung_Wf2_ThBwErwaermung_OManuelleAufheizung": (11538, 0, 0, 1),
    "Heizung_Wf2_ThBwErwaermung_TEin": (11530, 400, 0, 940),
    "Heizung_Wf2_ThBwErwaermung_TAus": (11533, 600, 10, 950),
    "Heizung_Heizkreis1_Kuehlung_Modus": (14066, 0, 0, 2),
    "Solar_Kol1_Omin": (3533, 0, 0, 1),
    "Solar_Kol1_Tmin": (3539, 200, 100, 900),
    "Heizung_Heizkreis1_Nachheizung_OFruehstart": (13714, 1, 0, 1),
    "Heizung_Heizkreis1_Nachheizung_OBlockierung": (13716, 1, 0, 1),
    "Heizung_Heizkreis1_Kuehlung_OTimer": (14098, 1, 0, 1),
    "Heizung_Wf2_OUrlaubAus": (11432, 0, 0, 1),
    "Heizung_Wf2_OSolarAus": (11433, 0, 0, 1),
    "Heizung_Wf2_SolarAus_OTSoll": (11435, 0, 0, 1),
    "Heizung_Heizkreis1_Nachheizung_OSolarAus": (14141, 0, 0, 1),
    "Heizung_Heizkreis1_Nachheizung_SolarAus_OTSoll": (14143, 0, 0, 1),
    "Solar_Wf8_NhUnterdrueckung_OSpSoll": (5302, 0, 0, 1),
    "Solar_Sp1_Tsoll": (3588, 500, 40, 950),
    "Solar_Sp1_Tmax": (3591, 800, 40, 950),
    "Anlage_Wf8_OSchaltuhr": (8331, 0, 0, 1),
    "Heizung_Wf3_BwVorerwaermung_dTAus": (11704, 40, 5, 495),
    "Heizung_Wf3_BwVorerwaermung_dTEin": (11701, 60, 10, 500),
    "Heizung_Wf3_BwVorerwaermung_ODifferenz": (11700, 1, 0, 1),
    "Anlage_Wf2_Feststoffkessel_Zieltemperatur_TZiel": (6933, 600, 300, 850),
    "Anlage_Wf2_Feststoffkessel_OZieltemperatur": (6932, 1, 0, 1),
    "Anlage_Wf5_Funktionsblock_Anstieg": (7736, 20, 5, 200),
    "Anlage_Wf5_Funktionsblock_dTSoll": (7717, 100, 20, 1000),
    "Anlage_Wf5_Funktionsblock_dTAus": (7714, 40, 5, 495),
    "Anlage_Wf5_Funktionsblock_dTEin": (7711, 60, 10, 500),
    "Anlage_Wf5_Funktionsblock_TTh2Aus": (7729, 500, -400, 2500),
    "Anlage_Wf5_Funktionsblock_TTh2Ein": (7726, 600, -400, 2500),
    "Anlage_Wf5_Funktionsblock_TTh1Aus": (7723, 500, -400, 2500),
    "Anlage_Wf5_Funktionsblock_TTh1Ein": (7720, 400, -400, 2500),
    "Anlage_Wf5_Funktionsblock_ODifferenz": (7708, 1, 0, 1),
    "Anlage_Wf5_Funktionsblock_OThermostat2": (7710, 1, 0, 1),
    "Anlage_Wf5_Funktionsblock_OThermostat1": (7709, 1, 0, 1),
    "Anlage_Wf7_Ruecklaufanhebung_OSommerAus": (8200, 1, 0, 1),
    "Anlage_Wf7_Ruecklaufanhebung_TSommerAus": (8207, 200, 100, 600),
    "Heizung_Heizkreis1_OEnergiesparbetrieb": (14158, 1, 0, 1),
    "Heizung_Heizkreis1_Energiesparbetrieb_dTAus": (14163, 40, 10, 490),
    "Heizung_Heizkreis1_Energiesparbetrieb_dtPause": (14166, 15, 0, 60),
    "Heizung_Heizkreis1_Energiesparbetrieb_dtLaufzeit": (14167, 2, 0, 60),
    "Anlage_WTUmwaelzpumpe_ORuhemodus": (10650, 1, 0, 1),
    "Anlage_WTUmwaelzpumpe_ONachlauf": (10647, 1, 0, 1),
    "Solar_Wf1_ExtWT_OZieltemperatur": (4091, 1, 0, 1),
    "Heizung_Heizkreis2_OUrlaub": (14671, 0, 0, 1),
    "Heizung_Heizkreis1_OUrlaub": (14176, 0, 0, 1),
    "Heizung_Heizkreis1_Heizgrenze": (13740, 1, 0, 1),
    "Heizung_Heizkreis1_OHeizgrenze": (13739, 1, 0, 1),
    "Heizung_Wf2_ThBwErwaermung_ONachlaufzeit": (11527, 1, 0, 1),
    "Heizung_Wf2_ThBwErwaermung_Nachlaufzeit": (11528, 4, 1, 10),
    "Solar_OSpreizladung": (3424, 1, 0, 1),
    "Solar_OPumpenverzoegerung": (3422, 1, 0, 1),
    "Solar_OPausendrehzahl": (3420, 1, 0, 1),
    "Display_Invertiert": (18260, 0, 0, 1),
    "SDKarte_Logging_Intervall": (18234, 60, 1, 1200),
    "SDKarte_Logging_Methode": (18235, 0, 0, 1),
    "Anlage_Wf1_Mischer_TZiel": (6629, 600, 0, 1300),
    "Heizung_Heizkreis1_Kuehlung_Aussen_TAussen": (14072, 200, 200, 400),
    "Heizung_Heizkreis1_Kuehlung_Kuehlsystem_TVorlauf": (14076, 200, 50, 250),
    "Heizung_Heizkreis1_Kuehlung_Kuehlsystem_TAussen1": (14080, 300, 150, 450),
    "Heizung_Heizkreis1_Kuehlung_Kuehlsystem_TVorlauf1": (14083, 200, 50, 250),
    "Heizung_Heizkreis1_Kuehlung_Kuehlsystem_TAussen2": (14086, 400, 150, 450),
    "Heizung_Heizkreis1_Kuehlung_Kuehlsystem_TVorlauf2": (14089, 100, 50, 250),
    "Anlage_Wf1_Mischer_dtIntervall": (6625, 4, 1, 20),
    "Heizung_Heizkreis1_Heizkurve": (13698, 12, 3, 30),
    "Heizung_Heizkreis1_Heizsystem_Gerade_TAussen1": (13979, 100, -200, 200),
    "Heizung_Heizkreis1_Heizsystem_Gerade_TVorlauf1": (13982, 400, 200, 900),
    "Heizung_Heizkreis1_Heizsystem_Gerade_TAussen2": (13985, -100, -200, 200),
    "Heizung_Heizkreis1_Heizsystem_Gerade_TVorlauf2": (13988, 600, 200, 900),
    "Heizung_Heizkreis1_Heizsystem_Raum_TStart": (13991, 400, 200, 600),
    "Heizung_Heizkreis1_Heizsystem_Raumeinfluss_Faktor": (14174, 4, 1, 9),
    "Heizung_Heizkreis1_Heizsystem_Raumeinfluss_Abweichung": (14175, 2, 1, 3),
    "Heizung_Heizkreis1_MischerIntervall": (13719, 4, 1, 20),
    "Heizung_Heizkreis1_Mischer_dTEin": (14168, 15, 5, 50),
    "Heizung_Heizkreis1_Mischer_dTAus": (14171, 10, 0, 45),
    "Heizung_RelaisAnforderung1_Mindestlaufzeit_tMin": (10799, 10, 0, 120),
    "Heizung_RelaisAnforderung1_Ausgang_dTSollVorlauf": (10770, 50, 0, 200),
    "Heizung_RelaisAnforderung1_Ausgang_Vorlauf_dTHysterese": (10778, 10, 5, 200),
    "Heizung_RelaisAnforderung1_Ausgang_Vorlauf_dVKorrektur": (10781, 1, 0, 10),
    "Heizung_RelaisAnforderung1_Ausgang_Vorlauf_dtIntervall": (10782, 30, 10, 600),
    "Anlage_Wf5_Funktionsblock_dtMindestausschaltzeit": (7748, 60, 1, 1439),
    "Heizung_Heizkreis1_Nachheizung_dTEin": (13747, -50, -150, 445),
    "Heizung_Heizkreis1_Nachheizung_dTAus": (13750, 50, -145, 450),
    "Heizung_Heizkreis1_Nachheizung_tFruehstart": (13715, 60, 0, 300),
    "Heizung_Heizkreis1_Nachheizung_tBlockierung": (13717, 30, 0, 300),
    "Heizung_Heizkreis1_Kuehlung_Timer_tEin": (14099, 540, 0, 1425),
    "Heizung_Heizkreis1_Kuehlung_Timer_tAus": (14100, 1200, 0, 1425),
    "Anlage_WTUmwaelzpumpe_SollDrehzahl": (10646, 40, 20, 100),
    "Anlage_WTUmwaelzpumpe_Nachlaufzeit": (10648, 20, 0, 60),
    "Anlage_WTUmwaelzpumpe_RuhemodusDauer": (10651, 120, 5, 240),
    "Heizung_RelaisPumpe1_Anlauf_dtAnlauf": (11182, 60, 0, 300),
    "Heizung_RelaisPumpe1_Nachlauf_TNachlauf": (11184, 500, 100, 900),
    "Heizung_Heizkreis1_TSommerTag": (13741, 200, 0, 400),
    "Heizung_Heizkreis1_TSommerNacht": (13744, 0, 0, 400),
    "Heizung_Heizkreis1_tSommerTagEin": (13700, 540, 0, 1425),
    "Heizung_Heizkreis1_tSommerTagAus": (13701, 1260, 0, 1425),
    "Heizung_Heizkreis1_Raumthermostat1_dTAbsenkung": (13830, 50, 10, 200),
    "Urlaubsfunktion_Tage": (19063, 0, 0, 200),
    "Heizung_Heizkreis1_Betriebsart": (14014, 0, 0, 5),
    "Heizung_Heizkreis2_Betriebsart": (14509, 0, 0, 5),
    "Wmz1_OAlternativAnzeige": (17273, 0, 0, 1),
    "Wmz1_AlternativAnzeigeEinheit": (17274, 0, 0, 3),
    "Wmz1_AlternativAnzeigeFaktor": (17275, 5000000, 1, 1000000000),
    "Relais_Regler_R1_MinDrehzahl": (1974, 30, 20, 100),
    "Relais_Regler_R1_MaxDrehzahl": (1975, 80, 20, 100),
    "Relais_Modul1_R1_MinDrehzahl": (2170, 30, 20, 100),
    "Relais_Modul1_R1_MaxDrehzahl": (2171, 80, 20, 100),
    "Anlage_Wf2_Feststoffkessel_dTEin": (6914, 50, 20, 300),
    "Anlage_Wf2_Feststoffkessel_dTAus": (6917, 30, 10, 290),
    "Anlage_Wf2_Feststoffkessel_dTSoll": (6920, 100, 30, 400),
    "Anlage_Wf2_Feststoffkessel_TMax": (6923, 800, 40, 950),
    "Anlage_Wf2_Feststoffkessel_TMin": (6926, 600, 40, 950),
    "Anlage_Wf3_RLBeimischung_dTEin": (7261, 50, 10, 250),
    "Anlage_Wf3_RLBeimischung_dTAus": (7264, 30, 5, 240),
    "Anlage_Wf3_RLBeimischung_dTSoll": (7267, 70, -200, 250),
    "Anlage_Wf3_RLBeimischung_Mischerlaufzeit": (7273, 105, 10, 600),
    "Anlage_Wf3_RLBeimischung_Mischerstellzeit": (7274, 2, 1, 20),
    "Solar_Wf1_Roehrenkollektor_tBeginn": (3840, 360, 0, 1410),
    "Solar_Wf1_Roehrenkollektor_tEnde": (3841, 420, 0, 1410),
    "Solar_Wf1_Roehrenkollektor_Laufzeit": (3842, 30, 5, 600),
    "Solar_Wf1_Roehrenkollektor_Pausezeit": (3843, 10, 1, 60),
    "Solar_Wf1_ExtWT_Nachlaufzeit": (4090, 2, 0, 15),
    "Solar_Wf1_ExtWT_Mindestlaufzeit": (4092, 3, 1, 15),
    "Solar_Wf1_ExtWT_ZieltemperaturSekundaer": (4109, 500, 150, 950),
    "Solar_Wf1_ExtWT_dTein": (4112, 60, 10, 200),
    "Solar_Wf1_ExtWT_dTaus": (4115, 40, 5, 195),
    "Solar_Wf1_ExtWT_dTAnstieg": (4121, 20, 10, 300),
    "Solar_Sp1_Vorrang": (3600, 1, 1, 5),
    "Solar_Spreizladung_dTDifferenz": (3425, 300, 200, 900),
    "Anlage_Wf6_Waermeaustausch_HMinMax": (7912, 20, 5, 100),
    "Solar_Sp1_dTein": (3579, 60, 10, 200),
    "Solar_Sp1_dTaus": (3582, 40, 5, 195),
    "Solar_Sp1_dTsoll": (3585, 100, 15, 300),
    "Solar_Sp1_Hmax": (3594, 20, 1, 100),
    "Solar_Sp1_Anstieg": (3597, 20, 10, 200),
    "Solar_Sp1_tMin": (3601, 30, 0, 300),
    "Solar_Sp1_MinDrehzahl": (3602, 30, 20, 100),
    "Solar_Pendelpause": (3418, 2, 1, 5),
    "Solar_Umwaelzzeit": (3419, 15, 1, 60),
    "Solar_Pausendrehzahl": (3421, 30, 20, 100),
    "Solar_Pumpenverzoegerung": (3423, 15, 5, 600),
    "Solar_Wf1_Bypass_dTein": (3908, 60, 10, 200),
    "Solar_Wf1_Bypass_dTaus": (3911, 40, 5, 195),
    "Solar_Wf1_CsBypass_Einstrahlung": (3991, 200, 100, 500),
    "Solar_Wf1_CsBypass_Verzoegerung": (3992, 120, 10, 300),
    "Solar_Wf1_Zieltemperatur_Zieltemperatur": (4344, 600, 200, 1100),
    "Solar_Wf1_Zieltemperatur_Anstieg": (4347, 20, 10, 200),
    "Heizung_Wf1_Zirkulation_TEin": (11408, 400, 100, 590),
    "Heizung_Wf1_Zirkulation_TAus": (11411, 600, 110, 600),
}
# This peer deliberately models the selected-unit parent contract. It is
# independent software evidence, not a physical observation of this encoding.
COMPOUND_NAMES = (
    "Heizung_Heizkreis1_Raumthermostat1_dTRaum",
    "Heizung_Wf3_BwVorerwaermung_dTMax",
    "Heizung_Wf3_BwVorerwaermung_dTAus",
    "Heizung_Wf3_BwVorerwaermung_dTEin",
    "Anlage_Wf5_Funktionsblock_Anstieg",
    "Anlage_Wf5_Funktionsblock_dTSoll",
    "Anlage_Wf5_Funktionsblock_dTAus",
    "Anlage_Wf5_Funktionsblock_dTEin",
    "Heizung_Heizkreis1_Energiesparbetrieb_dTAus",
    "Solar_Spreizladung_dTDifferenz",
    "Heizung_RelaisAnforderung1_Ausgang_dTSollVorlauf",
    "Heizung_RelaisAnforderung1_Ausgang_Vorlauf_dTHysterese",
    "Anlage_Wf6_Waermeaustausch_HMinMax",
    "Heizung_Heizkreis1_Mischer_dTEin",
    "Heizung_Heizkreis1_Mischer_dTAus",
    "Anlage_Wf2_Feststoffkessel_dTEin",
    "Anlage_Wf2_Feststoffkessel_dTAus",
    "Anlage_Wf2_Feststoffkessel_dTSoll",
    "Solar_Sp1_dTein",
    "Solar_Sp1_dTaus",
    "Solar_Sp1_dTsoll",
    "Solar_Sp1_Hmax",
    "Solar_Sp1_Anstieg",
    "Anlage_Wf3_RLBeimischung_dTEin",
    "Anlage_Wf3_RLBeimischung_dTAus",
    "Anlage_Wf3_RLBeimischung_dTSoll",
    "Solar_Wf1_Bypass_dTein",
    "Solar_Wf1_Bypass_dTaus",
    "Solar_Wf1_Zieltemperatur_Anstieg",
    "Solar_Wf1_ExtWT_dTein",
    "Solar_Wf1_ExtWT_dTaus",
    "Solar_Wf1_ExtWT_dTAnstieg",
    "Heizung_Heizkreis1_Raumthermostat1_dTAbsenkung",
    "Heizung_Heizkreis1_Nachheizung_dTEin",
    "Heizung_Heizkreis1_Nachheizung_dTAus",
)
COMPOUND_ALIASES = {
    name + suffix: (CONFIG_SETTINGS[name][0] + delta, CONFIG_SETTINGS[name][0], factor)
    for name in COMPOUND_NAMES
    for suffix, delta, factor in (("_K", 1, 1), ("_R", 2, 2))
}
COMPOUND_PARENTS = {CONFIG_SETTINGS[name][0] for name in COMPOUND_NAMES}
TEMPERATURE_NAMES = (
    "Heizung_Heizkreis1_Raumthermostat1_TRaum",
    "Heizung_RelaisPumpe1_Anlauf_TAnlauf",
    "Heizung_Wf3_BwVorerwaermung_TMax",
    "Anlage_Wf2_Feststoffkessel_Zieltemperatur_TZiel",
    "Anlage_Wf5_Funktionsblock_TTh2Aus",
    "Anlage_Wf5_Funktionsblock_TTh2Ein",
    "Anlage_Wf5_Funktionsblock_TTh1Aus",
    "Anlage_Wf5_Funktionsblock_TTh1Ein",
    "Anlage_Wf7_Ruecklaufanhebung_TSommerAus",
    "Anlage_Wf2_Feststoffkessel_TMax",
    "Anlage_Wf2_Feststoffkessel_TMin",
    "Heizung_Wf1_Zirkulation_TEin",
    "Heizung_Wf1_Zirkulation_TAus",
    "Solar_Wf1_Zieltemperatur_Zieltemperatur",
    "Solar_Wf1_ExtWT_ZieltemperaturSekundaer",
    "Heizung_Heizkreis1_TSommerTag",
    "Heizung_Heizkreis1_TSommerNacht",
    "Heizung_RelaisPumpe1_Nachlauf_TNachlauf",
    "Heizung_Heizkreis1_Heizsystem_Gerade_TAussen1",
    "Heizung_Heizkreis1_Heizsystem_Gerade_TVorlauf1",
    "Heizung_Heizkreis1_Heizsystem_Gerade_TAussen2",
    "Heizung_Heizkreis1_Heizsystem_Gerade_TVorlauf2",
    "Heizung_Heizkreis1_Heizsystem_Raum_TStart",
    "Anlage_Wf1_Mischer_TZiel",
    "Heizung_Heizkreis1_Kuehlung_Aussen_TAussen",
    "Heizung_Heizkreis1_Kuehlung_Kuehlsystem_TVorlauf",
    "Heizung_Heizkreis1_Kuehlung_Kuehlsystem_TAussen1",
    "Heizung_Heizkreis1_Kuehlung_Kuehlsystem_TVorlauf1",
    "Heizung_Heizkreis1_Kuehlung_Kuehlsystem_TAussen2",
    "Heizung_Heizkreis1_Kuehlung_Kuehlsystem_TVorlauf2",
    "Solar_Sp1_Tsoll",
    "Solar_Sp1_Tmax",
    "Heizung_Wf2_ThBwErwaermung_TEin",
    "Heizung_Wf2_ThBwErwaermung_TAus",
    "Solar_Kol1_Tmin",
)
TEMPERATURE_PARENTS = {CONFIG_SETTINGS[name][0] for name in TEMPERATURE_NAMES}
TEMPERATURE_ALIASES = {
    name + suffix: (CONFIG_SETTINGS[name][0] + delta, CONFIG_SETTINGS[name][0], delta)
    for name in TEMPERATURE_NAMES
    for suffix, delta in (("_C", 1), ("_F", 2))
}


def temperature_wire(celsius_raw, fahrenheit):
    # Test values use exactly representable temperatures in both unit variants.
    # This is a peer fixture, not a claim about physical vendor rounding.
    return celsius_raw * 9 // 5 + 320 if fahrenheit else celsius_raw


class Peer:
    """Network-side test state; never connect to site devices."""

    mode = 4
    em_mode = 2
    em_present = True
    presence_lookup = True
    deny = False
    writes = 0
    saves = 0
    settings: ClassVar[dict[int, int]] = {
        56: 1,
        60: 0,
        61: 0,
        62: 0,
        63: 0,
        **{index: value for index, value, _, _ in CONFIG_SETTINGS.values()},
    }
    persistent_file = None
    weekly_file = None
    weekly_words: ClassVar[dict[int, list[int]]] = {
        18419: [0x50482820, -1, -1],
        18420: [-48326, -1, -1],
        **{index: [-1, -1, -1] for index in range(18432, 18439)},
        **{index: [-1, -1, -1] for index in range(18445, 18452)},
    }
    bulk_saves = 0
    fail_bulk_subindex = None
    drop_discovery_response = False
    drop_relay_response = False
    silence_relay_response = False
    drop_next_offer = False
    hold_next_offer = False
    offer_waiting = threading.Event()
    offer_release = threading.Event()
    events: ClassVar[list[str]] = []
    extra_lookups: ClassVar[dict[int, int]] = {}
    extra_values: ClassVar[dict[int, int]] = {}
    release_silence = 0
    release_other_first = False
    release_offer_after_write = False
    release_offer_count = 0
    redundant_release_commands = 0
    silent_offers = 0
    failed_release_sessions = 0
    sessions = 0
    reads = 0
    lookups = 0
    owner = threading.Lock()


# Index immutable wire aliases once; read mutable peer state at request time.
# Preserve the original precedence (temperature aliases, temperature parents,
# differential aliases/parents, weekly words, volatile, persistent, base).
_COMPOUND_BY_INDEX = {
    index: (parent, factor) for index, parent, factor in COMPOUND_ALIASES.values()
}
_TEMPERATURE_BY_INDEX = {
    index: (parent, delta) for index, parent, delta in TEMPERATURE_ALIASES.values()
}


def peer_scalar(index):
    Peer.reads += 1
    if index in _TEMPERATURE_BY_INDEX:
        parent, delta = _TEMPERATURE_BY_INDEX[index]
        return temperature_wire(Peer.settings[parent], delta == 2)
    if index in TEMPERATURE_PARENTS:
        return temperature_wire(Peer.settings[index], Peer.settings[60])
    if index in _COMPOUND_BY_INDEX:
        parent, factor = _COMPOUND_BY_INDEX[index]
        return Peer.settings[parent] * factor
    if index in COMPOUND_PARENTS:
        return Peer.settings[index] * (1 + Peer.settings[60])
    if index in Peer.weekly_words:
        return Peer.weekly_words[index][0]
    if index in Peer.extra_values:
        return Peer.extra_values[index]
    if index in Peer.settings:
        return Peer.settings[index]
    return {
        0: CHANGESET,
        86: 1,
        RELAY: Peer.mode,
        2015: 0,
        191: 1,
        193: int(Peer.em_present),
        EM_RELAY: Peer.em_mode,
        2169: 1,
    }[index]


def semantic_hash(name):
    value = 0
    for byte in name.encode("ascii"):
        value = (value * 33 + byte) & 0x7FFFFFFF
    return value


def live_packet(address):
    header = struct.pack("<HHBHB", 0x10, address, 0x10, 0x100, 1)
    frame = bytes([1, 2, 3, 4, 0])
    return (
        b"\xaa"
        + header
        + bytes([127 - (sum(header) & 127)])
        + frame
        + bytes([127 - (sum(frame) & 127)])
    )


def additional_relays():
    """Populate other verified local/EM mappings independently of the driver."""
    for prefix, indices, existing in (
        ("Relais_Regler", (1977, 1991, 2005, 2019, 2033), RELAY),
        ("Relais_Modul1", (2173, 2187, 2201, 2215, 2229), EM_RELAY),
    ):
        for number, index in enumerate(indices, 1):
            if index == existing:
                continue
            Peer.extra_lookups[semantic_hash(f"{prefix}_R{number}_Handbetrieb")] = index
            Peer.extra_lookups[semantic_hash(f"{prefix}_R{number}_ODrehzahl")] = (
                index - 4
            )
            Peer.extra_values[index] = 2
            Peer.extra_values[index - 4] = number % 2


# Independent fixture: exact BX Plus 2.08 catalogue indices and native SET
# byte evidence. The peer models the candidate word order, not proof of a
# physical controller's multiword mapping or persistence.
TIMER_VALUES = {
    "Heizung_Heizkreis1_Raumthermostat1_SchaltuhrNr": (13849, 3),
    "Heizung_Wf1_SchaltuhrNr": (11346, 2),
    "Wsu3_Belegt": (18440, 1),
    "Wsu3_Schaltuhr_Woche_Initialisiert": (18444, 1),
    "Wsu3_Schaltuhr_Woche_TagMo_Daten": (18445, -1),
    "Wsu3_Schaltuhr_Woche_TagDi_Daten": (18446, -1),
    "Wsu3_Schaltuhr_Woche_TagMi_Daten": (18447, -1),
    "Wsu3_Schaltuhr_Woche_TagDo_Daten": (18448, -1),
    "Wsu3_Schaltuhr_Woche_TagFr_Daten": (18449, -1),
    "Wsu3_Schaltuhr_Woche_TagSa_Daten": (18450, -1),
    "Wsu3_Schaltuhr_Woche_TagSo_Daten": (18451, -1),
    "Anlage_Wf8_SchaltuhrNr": (8377, 2),
    "Wsu2_Belegt": (18427, 1),
    "Wsu2_Schaltuhr_Woche_Initialisiert": (18431, 1),
    "Wsu2_Schaltuhr_Woche_TagMo_Daten": (18432, -1),
    "Wsu2_Schaltuhr_Woche_TagDi_Daten": (18433, -1),
    "Wsu2_Schaltuhr_Woche_TagMi_Daten": (18434, -1),
    "Wsu2_Schaltuhr_Woche_TagDo_Daten": (18435, -1),
    "Wsu2_Schaltuhr_Woche_TagFr_Daten": (18436, -1),
    "Wsu2_Schaltuhr_Woche_TagSa_Daten": (18437, -1),
    "Wsu2_Schaltuhr_Woche_TagSo_Daten": (18438, -1),
    "Wsu1_Belegt": (18414, 1),
    "Wsu1_Schaltuhr_Woche_Initialisiert": (18418, 1),
    "Wsu1_Schaltuhr_Woche_TagMo_Daten": (18419, 0x50482820),
    "Wsu1_Schaltuhr_Woche_TagDi_Daten": (18420, -48326),
    "Anlage_Wf1_SchaltuhrNr": (6620, 1),
    "Anlage_Wf1_OSchaltuhr": (6574, 0),
    "Anlage_Wf4_Type": (7312, 9),
    "Anlage_Wf4_Aktiviert_Modus": (7319, 1),
    "Anlage_Wf4_OSchaltuhr": (7327, 1),
    "Anlage_Wf4_SchaltuhrNr": (7373, 1),
}


def additional_configuration():
    # Independent full reference pool: inactive references are zero.
    for slot in range(1, 17):
        for family, base, stride in (
            ("Anlage_Wf", 6620, 251),
            ("Heizung_Wf", 11346, 151),
        ):
            name = f"{family}{slot}_SchaltuhrNr"
            index = base + stride * (slot - 1)
            Peer.extra_lookups[semantic_hash(name)] = index
            Peer.extra_values[index] = 0
    for slot in range(1, 8):
        root = f"Heizung_Heizkreis{slot}"
        index = 13817 + 495 * (slot - 1)
        Peer.extra_lookups[semantic_hash(root + "_SchaltuhrNr")] = index
        Peer.extra_values[index] = 0
        for room in range(1, 6):
            name = f"{root}_Raumthermostat{room}_SchaltuhrNr"
            ref = index + 32 * room
            Peer.extra_lookups[semantic_hash(name)] = ref
            Peer.extra_values[ref] = 0
    for name, (index, value) in CONFIG_SELECTORS.items():
        Peer.extra_lookups[semantic_hash(name)] = index
        Peer.extra_values[index] = value
    for name, (index, _, _, _) in CONFIG_SETTINGS.items():
        Peer.extra_lookups[semantic_hash(name)] = index
    for name, (index, _, _) in {**COMPOUND_ALIASES, **TEMPERATURE_ALIASES}.items():
        Peer.extra_lookups[semantic_hash(name)] = index
    for name, (index, value) in TIMER_VALUES.items():
        Peer.extra_lookups[semantic_hash(name)] = index
        Peer.extra_values[index] = value


def datagram(destination, source, command, index=0, value=0):
    """Independent VBus 2.0 frame encoder."""
    payload = struct.pack("<Hi", index, value)
    body = (
        struct.pack("<HHBH", destination, source, 0x20, command)
        + bytes(x & 127 for x in payload)
        + bytes([sum((x >> 7) << i for i, x in enumerate(payload))])
    )
    return bytes([170]) + body + bytes([127 - (sum(body) & 127)])


class VBusHandler(socketserver.StreamRequestHandler):
    def handle(self):
        self.wfile.write(b"+HELLO\r\n")
        if self.rfile.readline() != b"PASS local-test\r\n" or Peer.deny:
            self.wfile.write(b"-DENIED\r\n")
            return
        self.wfile.write(b"+OK\r\n")
        if self.rfile.readline() != b"DATA\r\n":
            return
        self.wfile.write(b"+OK\r\n")
        if Peer.drop_next_offer:
            Peer.drop_next_offer = False
            return
        if Peer.silent_offers:
            Peer.silent_offers -= 1
            threading.Event().wait(20.2)
            return
        if Peer.hold_next_offer:
            Peer.hold_next_offer = False
            Peer.offer_waiting.set()
            assert Peer.offer_release.wait(timeout=10)
        assert Peer.owner.acquire(blocking=False), "overlapping bus owners"
        Peer.sessions += 1
        try:
            self.exchange()
        finally:
            Peer.owner.release()

    def exchange(self):
        relay_changed = False
        bulk_active = False
        resync_pending = False
        self.wfile.write(datagram(0, ADDRESS, 0x0500))
        while frame := self.rfile.read(16):
            assert len(frame) == 16 and frame[0] == 170
            assert sum(frame[1:]) & 127 == 127
            destination, source, proto, command = struct.unpack("<HHBH", frame[1:8])
            assert (destination, source, proto) == (ADDRESS, 0x20, 0x20)
            payload = bytes(
                x | (((frame[14] >> i) & 1) << 7) for i, x in enumerate(frame[8:14])
            )
            index, value = struct.unpack("<Hi", payload)
            if command == 0x1100:
                Peer.lookups += 1
                if value == 613666522:
                    Peer.events.append("missing")
                if value == 463603356 and Peer.silence_relay_response:
                    Peer.silence_relay_response = False
                    resync_pending = True
                    continue
                if value == 463603356 and Peer.drop_relay_response:
                    Peer.drop_relay_response = False
                    return
                if value == 613666522 and Peer.drop_discovery_response:
                    Peer.drop_discovery_response = False
                    return
                index = {
                    1222011281: 86,
                    463603356: RELAY,
                    530579973: 2015,
                    1121009828: 191,
                    1682631798: 193,
                    1215980970: EM_RELAY,
                    152651155: 2169,
                    852441249: 56,
                    156054146: 60,
                    124814937: 61,
                    1011665082: 62,
                    1893665643: 63,
                }.get(value, Peer.extra_lookups.get(value, 0))
                if value == 1682631798 and not Peer.presence_lookup:
                    index = 0
                response = datagram(source, ADDRESS, 0x1101, index, value)
            elif command == 0x0300:
                value = peer_scalar(index)
                response = datagram(source, ADDRESS, 0x0100, index, value)
            elif command in (0x0301, 0x0302):
                Peer.reads += 1
                assert index in Peer.weekly_words
                subindex = command & 0xFF
                if index == 18420 and subindex == 2:
                    response = datagram(source, ADDRESS, 0x4302, index)
                else:
                    response = datagram(
                        source,
                        ADDRESS,
                        0x0100 | subindex,
                        index,
                        Peer.weekly_words[index][subindex],
                    )
            elif command == 0x1300:
                response = datagram(source, ADDRESS, 0x1301, index, 0x08)
            elif command == 0x1400:
                assert not bulk_active and value > 0
                bulk_active = True
                response = datagram(source, ADDRESS, 0x1401, index)
            elif command in (0x1500, 0x1501, 0x1502):
                assert bulk_active and index == 18419
                assert Peer.extra_values[18414] == Peer.extra_values[18418] == 1
                assert Peer.extra_values[7373] == 1
                subindex = command & 0xFF
                # Native bulk writes mutate immediately; no staging buffer.
                Peer.weekly_words[index][subindex] = value
                if Peer.fail_bulk_subindex == subindex:
                    Peer.fail_bulk_subindex = None
                    return
                response = datagram(source, ADDRESS, 0x1600 | subindex, index)
            elif command == 0x1402:
                assert bulk_active
                bulk_active = False
                Peer.bulk_saves += 1
                temporary = Peer.weekly_file.with_suffix(".tmp")
                temporary.write_text(json.dumps(Peer.weekly_words), encoding="utf-8")
                temporary.replace(Peer.weekly_file)
                response = datagram(source, ADDRESS, 0x1403, index)
            elif command == 0x0200:
                if index == RELAY:
                    assert value in (0, 2, 4)
                    Peer.mode = value
                elif index == 2612:
                    assert Peer.extra_values[2602] == 7
                    assert value in (0, 2, 4)
                    Peer.extra_values[index] = value
                else:
                    assert index == EM_RELAY and Peer.em_present
                    assert value in (0, 1, 2, 3)
                    Peer.em_mode = value
                Peer.writes += 1
                relay_changed = True
                Peer.events.append("relay_write")
                response = datagram(source, ADDRESS, 0x0100, index, value)
            elif command == 0x0400:
                assert index in Peer.settings
                bounds = {
                    idx: (low, high) for idx, _, low, high in CONFIG_SETTINGS.values()
                }
                low, high = bounds.get(index, (0, 1))
                stored_value = value
                if index in COMPOUND_PARENTS:
                    factor = 1 + Peer.settings[60]
                    assert value % factor == 0
                    stored_value = value // factor
                    step = (
                        5
                        if index
                        in (
                            3579,
                            3582,
                            3585,
                            7912,
                            10778,
                            7261,
                            7264,
                            3908,
                            3911,
                            4347,
                            4112,
                            4115,
                            13747,
                            13750,
                        )
                        else 10
                    )
                    if index in (14168, 14171):
                        step = 1
                    assert stored_value % step == 0
                    proposed = {
                        parent: stored_value
                        if parent == index
                        else Peer.settings[parent]
                        for parent in COMPOUND_PARENTS
                    }
                    if index in (14168, 14171):
                        assert proposed[14168] >= proposed[14171] + 5
                    elif index in (10770, 10778):
                        assert Peer.extra_values[10724] == 1
                        assert Peer.extra_values[10727] == 1
                        assert Peer.extra_values[10744] == 1
                        assert 1 <= Peer.extra_values[10745] <= 4
                        if index == 10778:
                            assert Peer.extra_values[10773] == 1
                            assert Peer.extra_values[10775] > 0
                    elif index == 3425:
                        assert Peer.extra_values[3439] == Peer.settings[3424] == 1
                        assert Peer.extra_values[3416] == 20201
                        assert (
                            Peer.extra_values[3452] > 0 and Peer.extra_values[3536] > 0
                        )
                        assert (
                            Peer.extra_values[3576] > 0 and Peer.extra_values[3612] > 0
                        )
                        assert Peer.settings[3604] == Peer.settings[3640] == 1
                    elif index == 7912:
                        assert Peer.extra_values[7814] == 5
                        assert Peer.extra_values[7821] == 1
                        assert all(Peer.extra_values[i] > 0 for i in (7831, 7835, 7847))
                    elif index in (3579, 3582, 3585):
                        assert Peer.extra_values[3452] > 0
                        assert Peer.extra_values[3536] > 0
                        assert Peer.extra_values[3576] > 0
                        assert Peer.settings[3604] == 1
                        assert proposed[3582] < proposed[3579] < proposed[3585]
                    elif index in (7261, 7264, 7267):
                        assert Peer.extra_values[7061] == 11
                        assert Peer.extra_values[7068] == 1
                        assert all(
                            Peer.extra_values[i] > 0
                            for i in (7078, 7082, 7086, 7094, 7102)
                        )
                        assert proposed[7261] > proposed[7264]
                    elif index in (3908, 3911):
                        assert Peer.settings[3888] == 1
                        assert Peer.extra_values[3894] in (0, 1)
                        assert Peer.extra_values[3896] > 0
                        assert Peer.extra_values[3900] > 0
                        assert Peer.extra_values[3452] > 0
                        assert Peer.extra_values[3893] == 3
                        assert (
                            Peer.extra_values[3536] > 0 and Peer.extra_values[3550] > 0
                        )
                        assert proposed[3908] > proposed[3911]
                    elif index in (4112, 4115, 4121):
                        assert proposed[4112] >= proposed[4115] + 5
                        if index == 4121:
                            assert Peer.settings[4091] == 1
                            assert Peer.extra_values[4098] > 0
                    elif index in (13747, 13750):
                        assert proposed[13750] >= proposed[13747] + 5
                    elif index == 13830:
                        assert Peer.extra_values[13819] == 1
                        assert Peer.settings[13820] == 1
                        assert Peer.extra_values[13821] == 1
                        assert Peer.extra_values[13823] == 0
                        assert Peer.extra_values[13834] > 0
                    elif index == 4347:
                        assert Peer.settings[4335] == 1
                        assert Peer.extra_values[4341] > 0
                        assert Peer.extra_values[3452] > 0
                        assert Peer.extra_values[3536] > 0
                        assert Peer.extra_values[3576] > 0 and Peer.settings[3604] == 1
                    elif index in (6914, 6917, 6920):
                        assert proposed[6917] < proposed[6914] < proposed[6920]
                    elif index == 13827:
                        assert Peer.extra_values[13819] == 1
                        assert Peer.settings[13820] == 1
                if index in TEMPERATURE_PARENTS:
                    if index in (11408, 11411):
                        assert Peer.extra_values[11266] == 4
                        assert Peer.settings[11273] == 1
                        assert Peer.extra_values[11405] == 0
                    elif index == 11184:
                        assert Peer.extra_values[11166] == 2
                        assert Peer.extra_values[11169] == 1
                        assert Peer.extra_values[11171] > 0
                        assert Peer.settings[11183] == 1
                        assert Peer.extra_values[11189] > 0
                    elif index in (13741, 13744):
                        assert Peer.settings[13739] == 1
                        assert Peer.settings[13740] in (
                            (0, 1) if index == 13741 else (1,)
                        )
                        assert Peer.extra_values[13800] > 0
                    elif index == 4109:
                        assert Peer.settings[4091] == 1
                        assert Peer.extra_values[4098] > 0
                    elif index == 4344:
                        assert Peer.settings[4335] == 1
                        assert Peer.extra_values[4341] > 0
                        assert Peer.extra_values[3452] > 0
                        assert Peer.extra_values[3536] > 0
                        assert Peer.extra_values[3576] > 0 and Peer.settings[3604] == 1
                    elif index == 6629:
                        assert Peer.extra_values[6559] == 2
                        assert Peer.settings[6566] == 1
                        assert all(Peer.extra_values[i] > 0 for i in (6576, 6592, 6600))
                    elif index in (14072, 14076, 14080, 14083, 14086, 14089):
                        assert Peer.extra_values[13684] == 1
                        assert Peer.settings[13689] == 1
                        assert Peer.extra_values[13697] in (1, 2)
                        assert Peer.extra_values[14059] > 0
                        if index == 14072:
                            assert Peer.settings[14066] in (0, 2)
                            assert Peer.extra_values[13800] > 0
                        else:
                            assert Peer.extra_values[13796] > 0
                            assert Peer.extra_values[14075] == (
                                0 if index == 14076 else 1
                            )
                            if index != 14076:
                                assert Peer.extra_values[13800] > 0
                    elif index in (13979, 13982, 13985, 13988, 13991):
                        assert Peer.extra_values[13978] == (4 if index == 13991 else 3)
                        assert Peer.extra_values[13796] > 0
                        if index != 13991:
                            assert Peer.extra_values[13800] > 0
                    elif index in (6923, 6926, 6933):
                        assert Peer.extra_values[6810] == 6
                        assert Peer.extra_values[6817] == 1
                    if Peer.settings[60]:
                        assert (value - 320) * 5 % 9 == 0
                        stored_value = (value - 320) * 5 // 9
                    assert stored_value % 10 == 0
                    if index == 11408:
                        assert stored_value < Peer.settings[11411]
                    elif index == 11411:
                        assert stored_value > Peer.settings[11408]
                assert low <= stored_value <= high
                # Peer independently verifies installed program constraints.
                if index == 3600:
                    assert Peer.extra_values[3416] == 20201
                    assert Peer.extra_values[3576] > 0 and Peer.settings[3604] == 1
                    assert Peer.extra_values[3536] > 0 and Peer.extra_values[3452] > 0
                    assert 1 <= value <= 2
                if index in (18234, 18235):
                    assert Peer.extra_values[18227] == 1
                    assert all(Peer.extra_values[i] == 0 for i in range(18228, 18233))
                if index in (13991, 14174, 14175):
                    assert Peer.extra_values[13819] == 1
                    assert Peer.settings[13820] == 1
                    assert Peer.extra_values[13823] == 0
                    assert Peer.extra_values[13834] > 0
                    assert Peer.extra_values[13796] > 0
                    if index == 14174:
                        assert Peer.extra_values[13978] == 5
                    elif index == 14175:
                        assert Peer.extra_values[13978] in (4, 5)
                if index in (13719, 14168, 14171):
                    assert Peer.extra_values[13684] == 1
                    assert Peer.settings[13689] == 1
                    assert Peer.extra_values[13697] in (0, 2)
                    assert Peer.extra_values[13796] > 0
                    assert Peer.extra_values[13772] > 0
                    assert Peer.extra_values[13780] > 0
                if index in (10781, 10782):
                    assert Peer.extra_values[10724] == Peer.extra_values[10727] == 1
                    assert Peer.extra_values[10744] == Peer.extra_values[10773] == 1
                    assert 1 <= Peer.extra_values[10745] <= 4
                    assert Peer.extra_values[10775] > 0
                    if index == 10782:
                        assert value % 5 == 0
                if index == 10799:
                    assert Peer.extra_values[10724] == 1
                    assert Peer.extra_values[10727] == 1
                    assert Peer.settings[10798] == 1
                    assert Peer.extra_values[10734] == 1
                    assert Peer.extra_values[10735] > 0
                    assert value % 5 == 0
                if index == 7748:
                    assert Peer.extra_values[7563] == 9
                    assert Peer.extra_values[7570] == 1
                    assert Peer.settings[7747] == 1
                    assert Peer.extra_values[7596] > 0
                if index in (13747, 13750, 13715, 13717):
                    assert Peer.extra_values[13705] == Peer.extra_values[13708] == 1
                    assert Peer.extra_values[14003] > 0
                    assert Peer.extra_values[13804] > 0
                    if Peer.settings[13706] == 1:
                        assert Peer.extra_values[13808] > 0
                    else:
                        assert Peer.settings[13706] == 0
                    if index == 13715:
                        assert Peer.settings[13714] == 1
                    elif index == 13717:
                        assert Peer.settings[13716] == 1
                if index in (14099, 14100):
                    assert Peer.extra_values[13697] in (1, 2)
                    assert Peer.settings[14098] == 1
                    assert Peer.extra_values[14059] > 0 and value % 15 == 0
                if index in (10646, 10648, 10651):
                    assert Peer.extra_values[10607] == 1
                    assert Peer.extra_values[10614] > 0
                    if index == 10648:
                        assert Peer.settings[10647] == 1
                    elif index == 10651:
                        assert Peer.settings[10650] == 1 and value % 5 == 0
                if index == 11182:
                    assert Peer.extra_values[11166] == 2
                    assert Peer.extra_values[11169] == 1
                    assert Peer.extra_values[11171] > 0
                    assert Peer.settings[11178] == 2
                if index in (13700, 13701):
                    assert Peer.settings[13739] == Peer.settings[13740] == 1
                    assert Peer.extra_values[13800] > 0 and value % 15 == 0
                if index == 19063:
                    assert Peer.extra_values[68] == 1
                if index in (14014, 14509):
                    if index == 14509:
                        assert Peer.extra_values[14534] == 0
                    offset = index - 14014
                    assert Peer.extra_values[13684 + offset] == 1
                    assert Peer.settings[13689 + offset] == 1
                    if value == 4:
                        assert 1 <= Peer.settings[19063] <= 200
                    if value == 0:
                        assert (
                            not Peer.settings[14176 + offset]
                            or not Peer.settings[19063]
                        )
                if index in (3840, 3841, 3842, 3843):
                    assert Peer.extra_values[3416] == 20201
                    assert Peer.settings[3834] == 1
                    assert Peer.extra_values[3839] == 3
                    assert Peer.extra_values[3536] > 0
                    assert Peer.extra_values[3550] > 0
                    assert Peer.extra_values[3452] > 0
                    if index == 3840:
                        assert value % 30 == 0 and value + 30 <= Peer.settings[3841]
                    elif index == 3841:
                        assert value % 30 == 0 and value >= Peer.settings[3840] + 30
                    elif index == 3842:
                        assert value % 5 == 0
                if index in (4090, 4092, 4109, 4112, 4115, 4121):
                    assert Peer.extra_values[3416] == 20201
                    assert Peer.extra_values[4089] == 3
                    assert Peer.extra_values[4094] > 0 and Peer.extra_values[4102] > 0
                    assert Peer.extra_values[3452] > 0
                    assert Peer.extra_values[3576] > 0 and Peer.extra_values[3612] > 0
                    assert Peer.settings[3604] == Peer.settings[3640] == 1
                if index in (3991, 3992):
                    assert Peer.settings[3981] == 1
                    assert Peer.extra_values[3988] > 0
                    assert Peer.extra_values[3986] == 1
                    assert Peer.extra_values[3536] > 0 and Peer.extra_values[3452] > 0
                    if index == 3991:
                        assert value % 50 == 0
                if index in (7273, 7274):
                    assert Peer.extra_values[7061] == 11
                    assert Peer.extra_values[7068] == 1
                    assert all(
                        Peer.extra_values[i] > 0 for i in (7078, 7082, 7086, 7094, 7102)
                    )
                if index in (1974, 1975):
                    assert Peer.extra_values[1977] == 2
                    assert Peer.extra_values[1973] == 1
                    assert (
                        value <= Peer.settings[1975]
                        if index == 1974
                        else value >= Peer.settings[1974]
                    )
                if index in (2170, 2171):
                    assert Peer.em_present and Peer.em_mode == 2
                    assert Peer.extra_values[2167] == 2
                    assert (
                        value <= Peer.settings[2171]
                        if index == 2170
                        else value >= Peer.settings[2170]
                    )
                if index in (17274, 17275):
                    assert Peer.settings[17273] == 1
                if index in (3418, 3419, 3421, 3423):
                    assert Peer.extra_values[3452] > 0
                    assert Peer.extra_values[3536] > 0
                    assert Peer.extra_values[3576] > 0
                    assert Peer.settings[3604] == 1
                    if index == 3423:
                        assert Peer.settings[3422] == 1
                        assert value % 5 == 0
                    else:
                        assert Peer.extra_values[3612] > 0
                        assert Peer.settings[3640] == 1
                        assert Peer.settings[3417] == 0
                        if index == 3421:
                            assert Peer.settings[3420] == 1
                Peer.settings[index] = stored_value
                Peer.saves += 1
                Peer.events.append("save")
                temporary = Peer.persistent_file.with_suffix(".tmp")
                temporary.write_text(json.dumps(Peer.settings), encoding="utf-8")
                temporary.replace(Peer.persistent_file)
                response = datagram(source, ADDRESS, 0x0100, index, value)
            elif command == 0x0600:
                if resync_pending:
                    resync_pending = False
                    # Resume the native bus, then issue a new offer on this
                    # same authenticated connection. No mutation occurred.
                    self.wfile.write(live_packet(ADDRESS))
                    self.wfile.write(datagram(0, ADDRESS, 0x0500))
                    continue
                if Peer.failed_release_sessions:
                    Peer.failed_release_sessions -= 1
                    return
                if Peer.release_silence:
                    Peer.release_silence -= 1
                    continue
                if Peer.release_offer_after_write and relay_changed:
                    # Regression: after a real write the master resumes with
                    # a fresh offer rather than a VBus 1.0 live-data packet.
                    packet = datagram(0, ADDRESS, 0x0500)
                    self.wfile.write(packet[:9])
                    threading.Event().wait(0.03)
                    self.wfile.write(packet[9:])
                    Peer.release_offer_count += 1
                    # The client must not send another command on this
                    # released session. It must close and acquire afresh.
                    self.connection.settimeout(3)
                    if self.rfile.read(16):
                        Peer.redundant_release_commands += 1
                    return
                packet = live_packet(ADDRESS)
                if Peer.release_other_first:
                    Peer.release_other_first = False
                    self.wfile.write(live_packet(0x6651))
                    # The controller's real frame is late and fragmented.
                    threading.Event().wait(1.7)
                    self.wfile.write(packet[:7])
                    threading.Event().wait(0.03)
                    self.wfile.write(packet[7:])
                else:
                    self.wfile.write(packet)
                return
            else:
                raise AssertionError(f"Unexpected command {command}")
            self.wfile.write(response)


class VBusServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


class HttpHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        assert self.path == "/cgi-bin/get_resol_device_information"
        serial = (
            "isolated-secondary"
            if self.server.server_port == 18081
            else "isolated-gateway"
        )
        data = (
            f'product = "KM2"\nvendor = "RESOL"\nserial = "{serial}"\n'
            'version = "2.0"\nbuild = "test"\nname = "KM2"\nfeatures = "VBus"\n'
        )
        self.send(data.encode())

    def do_POST(self):
        assert self.path == "/cgi-bin/resol-webservice"
        raw = self.rfile.read(int(self.headers["Content-Length"])).decode()
        request = ast.literal_eval(raw)[0]
        if request["method"] == "login":
            assert request["params"] == {"username": "admin", "password": "http-test"}
            result = {"authId": "test-auth"}
        else:
            assert request["method"] == "dataGetCurrentData"
            assert request["params"]["authId"] == "test-auth"
            result = {
                "headers": [
                    {
                        "id": "00_0010_7112_0100",
                        "description": "BX Plus",
                        "destination_name": "Broadcast",
                        "source_name": "DeltaSol BX Plus",
                        "fields": [
                            {"id": "000_2_0", "name": "Temperature S1", "unit": "°C"},
                            {"id": "043_1_0", "name": "Relay 4", "unit": "%"},
                        ],
                    }
                ],
                "headersets": [
                    {
                        "packets": [
                            {"field_values": [{"raw_value": 42.0}, {"raw_value": 100}]}
                        ]
                    }
                ],
            }
            result["headers"].append(
                {
                    "id": "00_0010_7131_0100",
                    "description": "HQM #1",
                    "destination_name": "Broadcast",
                    "source_name": "DeltaSol BX Plus [HQM #1]",
                    "fields": [{"id": "004_4_0", "name": "Power", "unit": "W"}],
                }
            )
            result["headersets"][0]["packets"].append(
                {"field_values": [{"raw_value": 1000.0}]}
            )
            if Peer.em_present:
                result["headers"].append(
                    {
                        "id": "00_7112_6651_0100",
                        "description": "EM #1",
                        "destination_name": "BX Plus",
                        "source_name": "EM #1",
                        "fields": [
                            {"id": "000_4_0", "name": "Temperature S1", "unit": "°C"}
                        ],
                    }
                )
                result["headersets"][0]["packets"].append(
                    {"field_values": [{"raw_value": 30.0}]}
                )
        self.send(json.dumps([{"result": result}]).encode(), "application/json")

    def send(self, data, content_type="text/plain"):
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


async def qualify(directory, phase):
    from homeassistant import bootstrap, loader
    from homeassistant.config_entries import ConfigEntry, ConfigEntryState
    from homeassistant.core import Context, HomeAssistant
    from homeassistant.exceptions import HomeAssistantError
    from homeassistant.helpers import entity_registry as er

    hass = HomeAssistant(str(directory))
    loader.async_setup(hass)
    hass.config.skip_pip = True
    config = {
        "homeassistant": {
            "name": "Isolated DeltaSol qualification",
            "latitude": 0,
            "longitude": 0,
            "elevation": 0,
            "unit_system": "metric",
            "time_zone": "UTC",
        },
        "deltasol": {},
    }
    if phase in ("upgrade", "restart"):
        config.update({"http": {"server_port": 18123}, "frontend": {}})
    # Load the installed candidate's own catalogue types from the actual
    # isolated HA config directory, before entry setup can start Discovery.
    sys.path.insert(0, str(directory))
    from custom_components.deltasol.catalog import Catalog, CatalogError, Definition

    # Restrict only the independent peer's long discovery workload. The
    # candidate still loads the complete, checksum-verified vendor archive
    # and binds all runtime contracts against its original definitions.
    # The separate 3,200-item soak and exact-archive source test retain
    # large-inventory coverage. This is not a full physical catalogue scan.
    qualification_names = {
        "Schema",
        "Relais_Regler_R4_Handbetrieb",
        "AutoSommerWinterzeit",
        "TemperatureHysteresisSelector",
        "FlowSelector",
        "PressureSelector",
        "EnergySelector",
        *CONFIG_SELECTORS,
        *CONFIG_SETTINGS,
        *COMPOUND_ALIASES,
        *TEMPERATURE_ALIASES,
        *TIMER_VALUES,
    }

    def assert_weekly_evidence(diagnostic):
        weekly = diagnostic["configuration_discovery"]["weekly_programs"]
        assert weekly["write_access"] == "not_verified"
        week = weekly["programs"]["Wsu1"]
        assert week["allocated"] and week["initialized"]
        monday = week["days"]["Mo"]
        assert monday["observed_words"] == Peer.weekly_words[18419]
        assert monday["candidate_status"] == "wire_word_mapping_verified"
        assert monday["payload_verified"]
        expected_start = 480 if Peer.weekly_words[18419][0] == 0x50482820 else 510
        assert monday["candidate_windows"] == [
            {"start_minute": expected_start, "end_minute": 600},
            {"start_minute": 1080, "end_minute": 1200},
        ]
        tuesday = week["days"]["Di"]
        assert tuesday["observed_words"] == [-48326, -1, None]
        assert "candidate_windows" not in tuesday
        assert not tuesday["payload_verified"]
        assert (
            weekly["references"]["Anlage_Wf1_SchaltuhrNr"]["candidate_program"]
            == "Wsu1"
        )

    candidate_filter = nullcontext()
    if phase in ("upgrade", "restart"):
        from custom_components.deltasol.discovery import ConfigurationCapture

        original_base = ConfigurationCapture.base_candidates

        def qualification_base(capture):
            base = original_base.__get__(capture, type(capture))
            if capture.catalog.matches(ADDRESS, CHANGESET):
                return tuple(d for d in base if d.name in qualification_names)
            return base

        candidate_filter = patch.object(
            ConfigurationCapture, "base_candidates", property(qualification_base)
        )
    candidate_filter.__enter__()
    semantic_pacing = ExitStack()

    fixture = Catalog(
        "isolated-test-catalogue",
        (
            Definition(
                "Schema", 86, "unspecified", "Number", {"type_chain": ["Number"]}
            ),
            Definition(
                "Relais_Regler_R4_Handbetrieb", RELAY, "volatile", "Handbetrieb", {}
            ),
            Definition("Missing", 123, "unspecified", None, {}),
            Definition("Password", 124, "persistent", "Number", {}, "sensitive"),
            Definition("Bedienercode", 55, "unspecified", "Number", {}),
        ),
    )
    if phase == "upgrade":
        initial_catalog = patch(
            "custom_components.deltasol.load_catalog", return_value=fixture
        )
    elif phase == "restart":
        # A temporarily unusable catalogue must not overwrite the checkpoint.
        # The existing reload test below then restores the verified fixture.
        initial_catalog = patch(
            "custom_components.deltasol.load_catalog",
            side_effect=CatalogError("Qualification catalogue unavailable"),
        )
    else:
        initial_catalog = nullcontext()
    stopped = False
    try:
        with initial_catalog:
            assert await bootstrap.async_from_config_dict(config, hass) is hass
            await hass.async_start()
            await hass.async_block_till_done()
            if phase == "upgrade":
                (initial_entry,) = hass.config_entries.async_entries("deltasol")
                initial = initial_entry.runtime_data
                if initial.auto_start_task:
                    await initial.auto_start_task
                if initial.capture_task:
                    await initial.capture_task
                assert initial.capture.status == "complete"
                assert initial.capture.offset == 3 and Peer.writes == 0
        if phase == "baseline":
            flow = hass.config_entries.flow
            result = await flow.async_init(
                "deltasol",
                context={"source": "user"},
                data={"host": "127.0.0.1", "port": 18080},
            )
            assert result["step_id"] == "auth", result
            result = await flow.async_configure(
                result["flow_id"], {"username": "admin", "password": "http-test"}
            )
            assert result["step_id"] == "options", result
            result = await flow.async_configure(
                result["flow_id"], {"scan_interval": 60}
            )
            assert result["type"] == "create_entry", result
        await hass.async_block_till_done()
        (entry,) = hass.config_entries.async_entries("deltasol")
        assert entry.state == ConfigEntryState.LOADED, entry
        registry = er.async_get(hass)
        sensor = registry.async_get_entity_id("sensor", "deltasol", SENSOR_UID)
        assert sensor and float(hass.states.get(sensor).state) == 42
        baseline = directory / "qualification-baseline.json"
        identity = {"entry": entry.entry_id, "sensor": sensor, "data": dict(entry.data)}
        if phase == "baseline":
            options = hass.config_entries.options
            result = await options.async_init(entry.entry_id)
            assert result["step_id"] == "init", result
            with patch("custom_components.deltasol.load_catalog", return_value=fixture):
                result = await options.async_configure(
                    result["flow_id"], {"vbus_password": "local-test"}
                )
                assert result["type"] == "create_entry", result
                await hass.async_block_till_done()
                initial = entry.runtime_data
                if initial.auto_start_task:
                    await initial.auto_start_task
                if initial.capture_task:
                    await initial.capture_task
                assert initial.capture.status == "complete"
                assert initial.capture.offset == 3
        assert entry.options["vbus_password"] == "local-test"
        assert entry.state == ConfigEntryState.LOADED
        select = registry.async_get_entity_id("select", "deltasol", SELECT_UID)
        assert select
        identity["select"] = select
        em_sensor = registry.async_get_entity_id("sensor", "deltasol", EM_SENSOR_UID)
        assert em_sensor and float(hass.states.get(em_sensor).state) == 30
        identity["em_sensor"] = em_sensor
        hqm_sensor = registry.async_get_entity_id("sensor", "deltasol", HQM_SENSOR_UID)
        assert hqm_sensor and float(hass.states.get(hqm_sensor).state) == 1000
        identity["hqm_sensor"] = hqm_sensor
        em = registry.async_get_entity_id("select", "deltasol", EM_UID)
        assert em and hass.states.get(em).state == "Auto"
        identity["em_select"] = em
        identity["display"] = registry.async_get_entity_id(
            "select",
            "deltasol",
            "isolated-gateway_7112_TemperatureHysteresisSelector_configuration",
        )
        identity["daylight"] = registry.async_get_entity_id(
            "switch",
            "deltasol",
            "isolated-gateway_7112_AutoSommerWinterzeit_configuration",
        )
        assert identity["display"] and identity["daylight"]
        if phase == "baseline":
            baseline.write_text(json.dumps(identity), encoding="utf-8")
            assert Peer.writes == 0
            return
        assert json.loads(baseline.read_text(encoding="utf-8")) == identity
        if phase == "rollback":
            assert hass.states.get(em).attributes["options"] == [
                "Auto",
                "Off",
                "Min",
                "Max",
            ]
            assert Peer.writes == 0
            return

        assert hass.states.get(select).state == "On"
        assert hass.states.get(select).attributes["options"] == ["Auto", "Off", "On"]
        em = registry.async_get_entity_id("select", "deltasol", EM_UID)
        assert em and hass.states.get(em).state == "Auto"
        assert hass.states.get(em).attributes["options"] == [
            "Auto",
            "Off",
            "Min",
            "Max",
        ]
        assert (
            registry.async_get(em).device_id == registry.async_get(em_sensor).device_id
        )
        assert Peer.writes == 0
        from custom_components.deltasol.diagnostics import (
            async_get_config_entry_diagnostics,
        )

        if phase == "upgrade":
            duplicate = ConfigEntry(
                version=1,
                minor_version=1,
                domain="deltasol",
                title="Duplicate gateway",
                data={**entry.data, "host": "localhost"},
                source="user",
                unique_id="duplicate-isolated-gateway",
                options=dict(entry.options),
                discovery_keys=MappingProxyType({}),
                subentries_data=None,
            )
            sessions_before = Peer.sessions
            await hass.config_entries.async_add(duplicate)
            await hass.async_block_till_done()
            assert duplicate.state == ConfigEntryState.SETUP_ERROR
            assert Peer.sessions == sessions_before
            assert await hass.config_entries.async_remove(duplicate.entry_id)
            assert entry.state == ConfigEntryState.LOADED
        diagnostic = await async_get_config_entry_diagnostics(hass, entry)
        encoded = json.dumps(diagnostic)
        for secret in ("local-test", "http-test", "127.0.0.1", "isolated-gateway"):
            assert secret not in encoded
        assert diagnostic["telemetry"]["available"]
        access = diagnostic["parameter_access"]
        assert access["status"] == "supported"
        assert access["relays"]["R4"]["mode"] == "On"
        assert access["relays"]["R4"]["mode_value"] == 4
        assert access["relays"]["R1"]["writable_options"] == [
            "Auto",
            "Off",
            "Min",
            "Max",
        ]
        assert (
            sum(relay["status"] == "supported" for relay in access["relays"].values())
            == 10
        )
        assert access["relays"]["M1_R1"]["writable_options"] == [
            "Auto",
            "Off",
            "Min",
            "Max",
        ]
        assert access["relays"]["M2_R1"]["writable_options"] == []
        assert any(d["source_address"] == "0x6651" for d in access["devices"])
        flags = access["module_flags"]["M1"]
        assert flags["Modul1_Aktiviert"]["raw_value"] == 1
        assert flags["Modul1_Vorhanden"]["status"] == "read"
        assert Peer.writes == 0
        display = registry.async_get_entity_id(
            "select",
            "deltasol",
            "isolated-gateway_7112_TemperatureHysteresisSelector_configuration",
        )
        daylight = registry.async_get_entity_id(
            "switch",
            "deltasol",
            "isolated-gateway_7112_AutoSommerWinterzeit_configuration",
        )
        assert display and daylight
        assert (
            registry.async_get(display).device_id
            == registry.async_get(sensor).device_id
        )
        assert registry.async_get(display).entity_category == "config"
        assert hass.states.get(display).state == ("°F" if phase == "restart" else "°C")
        assert hass.states.get(daylight).state == (
            "off" if phase == "restart" else "on"
        )
        assert (
            diagnostic["configuration_controls"]["TemperatureHysteresisSelector"][
                "write_access"
            ]
            == "profile_verified"
        )
        assert Peer.saves == 0

        expected_config_count = len(qualification_names)
        button = registry.async_get_entity_id(
            "button", "deltasol", f"{entry.entry_id}_configuration_discovery"
        )
        assert button
        progress = registry.async_get_entity_id(
            "sensor", "deltasol", f"{entry.entry_id}_configuration_discovery_status"
        )
        expected_status = "complete" if phase == "upgrade" else "not_started"
        assert progress and hass.states.get(progress).state == expected_status
        percent = registry.async_get_entity_id(
            "sensor", "deltasol", f"{entry.entry_id}_configuration_discovery_progress"
        )
        assert percent and float(hass.states.get(percent).state) == (
            100 if phase == "upgrade" else 0
        )
        assert hass.states.get(percent).attributes["unit_of_measurement"] == "%"
        assert (
            registry.async_get(percent).device_id
            == registry.async_get(progress).device_id
        )
        for entity_id in (button, progress, percent):
            assert registry.async_get(entity_id).entity_category == "diagnostic"
        assert diagnostic["configuration_discovery"]["status"] == expected_status
        if phase == "restart":
            assert (
                diagnostic["configuration_discovery"]["checkpoint_error"]
                == "checkpoint_catalogue_unavailable"
            )
        with (
            nullcontext()
            if phase == "restart"
            else patch("custom_components.deltasol.load_catalog", return_value=fixture)
        ):
            if phase == "restart":
                # Restore through the real local archive loader, not a mock.
                assert await hass.config_entries.async_reload(entry.entry_id)
                resumed = entry.runtime_data.capture
                marker = json.loads(
                    (directory / "qualification-active.json").read_text(
                        encoding="utf-8"
                    )
                )
                assert resumed.started_at == marker["started_at"]
                assert resumed.offset >= marker["offset"]
                if entry.runtime_data.capture_task:
                    await entry.runtime_data.capture_task
                assert resumed.status == "complete"
                assert resumed.offset == expected_config_count
                assert resumed.catalog.matches(ADDRESS, CHANGESET)
                assert {d.name for d in resumed.candidates} == qualification_names
                assert_weekly_evidence(
                    await async_get_config_entry_diagnostics(hass, entry)
                )
                assert float(hass.states.get(percent).state) == 100
                assert Peer.writes == 0
                assert Peer.saves == 0
                assert Peer.settings[60] == 1 and Peer.settings[56] == 0
                await entry.runtime_data.relay_coordinator.async_refresh()
                await hass.async_block_till_done()
                for name, kind, value in (
                    ("Anlage_Wf1_Mischer_dtIntervall", "number", 5),
                    ("Heizung_Heizkreis1_Heizkurve", "number", 1.5),
                    ("Heizung_Heizkreis1_MischerIntervall", "number", 6),
                    ("Anlage_Wf1_Mischer_TZiel", "number", 158),
                    ("Display_Invertiert", "switch", "on"),
                    ("SDKarte_Logging_Intervall", "number", 120),
                    ("SDKarte_Logging_Methode", "select", "Cyclic (overwrite oldest)"),
                    ("Heizung_Heizkreis1_Kuehlung_Aussen_TAussen", "number", 86),
                    ("Heizung_Heizkreis1_Kuehlung_Kuehlsystem_TVorlauf", "number", 50),
                    ("Heizung_Heizkreis1_Mischer_dTEin", "number", 4),
                    ("Heizung_Heizkreis1_Mischer_dTAus", "number", 2.4),
                    ("Heizung_RelaisAnforderung1_Mindestlaufzeit_tMin", "number", 15),
                    ("Heizung_RelaisAnforderung1_Ausgang_dTSollVorlauf", "number", 14),
                    (
                        "Heizung_RelaisAnforderung1_Ausgang_Vorlauf_dTHysterese",
                        "number",
                        3,
                    ),
                    (
                        "Heizung_RelaisAnforderung1_Ausgang_Vorlauf_dVKorrektur",
                        "number",
                        0.2,
                    ),
                    (
                        "Heizung_RelaisAnforderung1_Ausgang_Vorlauf_dtIntervall",
                        "number",
                        35,
                    ),
                    ("Anlage_Wf5_Funktionsblock_dtMindestausschaltzeit", "number", 75),
                    ("Heizung_Heizkreis1_Betriebsart", "select", "Day"),
                    ("Heizung_Heizkreis1_Nachheizung_dTEin", "number", -9),
                    ("Heizung_Heizkreis1_Nachheizung_dTAus", "number", 12),
                    ("Heizung_Heizkreis1_Nachheizung_tFruehstart", "number", 90),
                    ("Heizung_Heizkreis1_Nachheizung_tBlockierung", "number", 45),
                    ("Heizung_Heizkreis1_Kuehlung_Timer_tEin", "number", 555),
                    ("Heizung_Heizkreis1_Kuehlung_Timer_tAus", "number", 1215),
                    ("Anlage_WTUmwaelzpumpe_SollDrehzahl", "number", 50),
                    ("Anlage_WTUmwaelzpumpe_Nachlaufzeit", "number", 30),
                    ("Anlage_WTUmwaelzpumpe_RuhemodusDauer", "number", 12.5),
                    ("Heizung_RelaisPumpe1_Anlauf_dtAnlauf", "number", 75),
                    ("Heizung_RelaisPumpe1_Nachlauf_TNachlauf", "number", 140),
                    ("Heizung_Heizkreis1_TSommerTag", "number", 86),
                    ("Heizung_Heizkreis1_TSommerNacht", "number", 50),
                    ("Heizung_Heizkreis1_tSommerTagEin", "number", 555),
                    ("Heizung_Heizkreis1_tSommerTagAus", "number", 1275),
                    ("Heizung_Heizkreis1_Raumthermostat1_dTAbsenkung", "number", 12),
                    ("Urlaubsfunktion_Tage", "number", 0),
                    ("Wmz1_OAlternativAnzeige", "switch", "on"),
                    ("Wmz1_AlternativAnzeigeEinheit", "select", "Gas"),
                    ("Wmz1_AlternativAnzeigeFaktor", "number", 0.5000001),
                    ("Relais_Modul1_R1_MinDrehzahl", "number", 40),
                    ("Relais_Modul1_R1_MaxDrehzahl", "number", 70),
                    ("Anlage_Wf2_Feststoffkessel_dTEin", "number", 14),
                    ("Relais_Regler_R1_MinDrehzahl", "number", 40),
                    ("Relais_Regler_R1_MaxDrehzahl", "number", 70),
                    ("Anlage_Wf2_Feststoffkessel_TMin", "number", 158),
                    ("Anlage_Wf2_Feststoffkessel_TMax", "number", 194),
                    ("Solar_Sp1_dTein", "number", 14),
                    ("Solar_Sp1_Vorrang", "number", 2),
                    ("Solar_Spreizladung_dTDifferenz", "number", 70),
                    ("Anlage_Wf6_Waermeaustausch_HMinMax", "number", 5),
                    ("Heizung_Wf1_Zirkulation_TEin", "number", 122),
                    ("Solar_Pendelpause", "number", 3),
                    ("Solar_Umwaelzzeit", "number", 20),
                    ("Solar_Pausendrehzahl", "number", 40),
                    ("Solar_Pumpenverzoegerung", "number", 25),
                    ("Solar_OPausendrehzahl", "switch", "on"),
                    ("Solar_OPumpenverzoegerung", "switch", "on"),
                    ("Solar_OSpreizladung", "switch", "on"),
                    ("Anlage_Wf8_OSchaltuhr", "switch", "on"),
                    ("Anlage_Wf5_Funktionsblock_OThermostat1", "switch", "on"),
                    ("Anlage_Wf5_Funktionsblock_OThermostat2", "switch", "on"),
                    ("Anlage_Wf5_Funktionsblock_ODifferenz", "switch", "on"),
                    ("Anlage_Wf2_Feststoffkessel_OZieltemperatur", "switch", "on"),
                    ("Heizung_Wf3_BwVorerwaermung_ODifferenz", "switch", "on"),
                    ("Anlage_Wf7_Ruecklaufanhebung_OSommerAus", "switch", "on"),
                    ("Heizung_Heizkreis1_OHeizgrenze", "switch", "on"),
                    ("Heizung_Heizkreis1_Heizgrenze", "select", "Day/Night"),
                    ("Heizung_Heizkreis1_OEnergiesparbetrieb", "switch", "on"),
                    ("Heizung_Heizkreis1_OUrlaub", "switch", "on"),
                    ("Solar_Wf1_ExtWT_OZieltemperatur", "switch", "on"),
                    ("Anlage_WTUmwaelzpumpe_ONachlauf", "switch", "on"),
                    ("Anlage_WTUmwaelzpumpe_ORuhemodus", "switch", "on"),
                    ("Heizung_Wf2_ThBwErwaermung_ONachlaufzeit", "switch", "on"),
                    ("Heizung_Wf2_ThBwErwaermung_Nachlaufzeit", "number", 4),
                    ("Anlage_Wf3_RLBeimischung_dTEin", "number", 11),
                    ("Anlage_Wf3_RLBeimischung_dTSoll", "number", -20),
                    ("Anlage_Wf3_RLBeimischung_Mischerlaufzeit", "number", 120),
                    ("Anlage_Wf3_RLBeimischung_Mischerstellzeit", "number", 5),
                    ("Solar_Wf1_Bypass_dTein", "number", 14),
                    ("Solar_Wf1_CsBypass_Einstrahlung", "number", 250),
                    ("Solar_Wf1_CsBypass_Verzoegerung", "number", 150),
                    ("Solar_Wf1_Zieltemperatur_Zieltemperatur", "number", 158),
                    ("Solar_Wf1_Zieltemperatur_Anstieg", "number", 6),
                    ("Solar_Wf1_Roehrenkollektor_tBeginn", "number", 390),
                    ("Solar_Wf1_Roehrenkollektor_tEnde", "number", 450),
                    ("Solar_Wf1_Roehrenkollektor_Laufzeit", "number", 40),
                    ("Solar_Wf1_Roehrenkollektor_Pausezeit", "number", 12),
                    ("Solar_Wf1_ExtWT_Nachlaufzeit", "number", 4),
                    ("Solar_Wf1_ExtWT_Mindestlaufzeit", "number", 5),
                    ("Solar_Wf1_ExtWT_dTein", "number", 14),
                    ("Solar_Wf1_ExtWT_dTaus", "number", 10),
                    ("Solar_Wf1_ExtWT_ZieltemperaturSekundaer", "number", 140),
                    ("Solar_Wf1_ExtWT_dTAnstieg", "number", 6),
                    (
                        "Wsu1_Schaltuhr_Woche_TagMo_Daten",
                        "text",
                        "08:30-10:00, 18:00-20:00",
                    ),
                ):
                    entity = registry.async_get_entity_id(
                        kind, "deltasol", f"isolated-gateway_7112_{name}_configuration"
                    )
                    assert entity
                    observed = hass.states.get(entity).state
                    assert (float(observed) if kind == "number" else observed) == value
                Peer.extra_values[14075] = 1
                await entry.runtime_data.relay_coordinator.async_refresh()
                await hass.async_block_till_done()
                for suffix, value in (
                    ("TAussen1", 68),
                    ("TVorlauf1", 50),
                    ("TAussen2", 86),
                    ("TVorlauf2", 68),
                ):
                    name = "Heizung_Heizkreis1_Kuehlung_Kuehlsystem_" + suffix
                    target = registry.async_get_entity_id(
                        "number",
                        "deltasol",
                        f"isolated-gateway_7112_{name}_configuration",
                    )
                    assert target and float(hass.states.get(target).state) == value
                Peer.extra_values[14075] = 0
                for mode, expected in (
                    (
                        3,
                        (
                            ("Gerade_TAussen1", "number", 32),
                            ("Gerade_TAussen2", "number", 32),
                            ("Gerade_TVorlauf1", "number", 122),
                            ("Gerade_TVorlauf2", "number", 158),
                        ),
                    ),
                    (
                        4,
                        (
                            ("Raum_TStart", "number", 122),
                            ("Raumeinfluss_Abweichung", "select", "Maximum"),
                        ),
                    ),
                    (5, (("Raumeinfluss_Faktor", "number", 7),)),
                ):
                    Peer.extra_values[13978] = mode
                    await entry.runtime_data.relay_coordinator.async_refresh()
                    await hass.async_block_till_done()
                    for suffix, kind, value in expected:
                        name = "Heizung_Heizkreis1_Heizsystem_" + suffix
                        entity = registry.async_get_entity_id(
                            kind,
                            "deltasol",
                            f"isolated-gateway_7112_{name}_configuration",
                        )
                        assert entity
                        observed = hass.states.get(entity).state
                        assert (
                            float(observed) if kind == "number" else observed
                        ) == value
                Peer.extra_values[13978] = 1
                assert Peer.saves == 0
                assert hass.states.get(display).state == "°F"
                assert hass.states.get(daylight).state == "off"
                assert em == json.loads(
                    (directory / "qualification-select.json").read_text(
                        encoding="utf-8"
                    )
                )
                return
            # A real transport failure is resumable and never becomes a write.
            Peer.deny = True
            await hass.services.async_call(
                "button", "press", {"entity_id": button}, blocking=True
            )
            await entry.runtime_data.capture_task
            capture = (await async_get_config_entry_diagnostics(hass, entry))[
                "configuration_discovery"
            ]
            assert capture["status"] == "incomplete", capture
            assert hass.states.get(progress).state == "incomplete"
            assert capture["scanned_count"] == 0
            assert float(hass.states.get(percent).state) == 0
            assert Peer.writes == 0
            Peer.deny = False
            # Baseline and rollback have already returned above.
            resilient = hasattr(entry.runtime_data.capture, "retry_count")
            assert resilient, "Candidate must implement automatic Discovery recovery"
            Peer.drop_discovery_response = True
            await hass.services.async_call(
                "button", "press", {"entity_id": button}, blocking=True
            )
            await entry.runtime_data.capture_task
            capture = (await async_get_config_entry_diagnostics(hass, entry))[
                "configuration_discovery"
            ]
            assert capture["status"] == "complete", capture
            assert capture["scanned_count"] == 3
            if resilient:
                assert capture["retry_count"] == 1
                assert capture["consecutive_failures"] == 0
                assert capture["last_failure"]["phase"] == "response"
                assert capture["last_failure"]["reference_index"] == 123
                assert not Peer.drop_discovery_response
            assert capture["parameters"]["Schema"]["decoded_value"] == 1
            assert capture["parameters"]["Missing"]["status"] == "not_resolved"
            assert "Password" not in capture["parameters"]
            assert "Bedienercode" not in capture["parameters"]
            assert "metadata" not in capture["parameters"]["Missing"]
            assert hass.states.get(progress).state == "complete"
            assert hass.states.get(progress).attributes["progress_percent"] == 100
            assert float(hass.states.get(percent).state) == 100
            attrs = hass.states.get(progress).attributes
            assert attrs["read_count"] == 2
            assert attrs["not_resolved_count"] == 1
            assert attrs["remaining_count"] == 0
            assert attrs["last_error"] is None
            assert attrs["started_at"] and attrs["finished_at"]
            assert capture["summary"]["progress_percent"] == 100
            assert Peer.writes == 0
            Peer.mode = 2
            await hass.services.async_call(
                "button", "press", {"entity_id": button}, blocking=True
            )
            await entry.runtime_data.capture_task
            capture = (await async_get_config_entry_diagnostics(hass, entry))[
                "configuration_discovery"
            ]
            change = capture["comparison"]["changes"]["Relais_Regler_R4_Handbetrieb"]
            assert change["after_raw"] == 2
            assert Peer.writes == 0
            Peer.mode = 4
            # Native storage must preserve the completed inventory and comparison
            # across a real config-entry unload/reload, without another scan.
            assert await hass.config_entries.async_reload(entry.entry_id)
            await hass.async_block_till_done()
            assert entry.runtime_data.capture.status == "complete"
            assert entry.runtime_data.capture_task is None
            restored = (await async_get_config_entry_diagnostics(hass, entry))[
                "configuration_discovery"
            ]
            assert restored["scanned_count"] == 3
            assert restored["summary"]["progress_percent"] == 100
            assert (
                restored["comparison"]["changes"]["Relais_Regler_R4_Handbetrieb"][
                    "after_raw"
                ]
                == 2
            )
            assert (
                registry.async_get_entity_id(
                    "sensor",
                    "deltasol",
                    f"{entry.entry_id}_configuration_discovery_progress",
                )
                == percent
            )
            assert float(hass.states.get(percent).state) == 100
            assert Peer.writes == 0
        if resilient:
            health = entry.runtime_data.coordinator.api.parameter_transport_health()
            before = health["read_resync_successes"]
            sessions = Peer.sessions
            Peer.silence_relay_response = True
            await entry.runtime_data.relay_coordinator.async_refresh()
            assert not Peer.silence_relay_response
            assert entry.runtime_data.relay_coordinator.last_update_success
            assert Peer.sessions == sessions + 1
            health = entry.runtime_data.coordinator.api.parameter_transport_health()
            assert health["read_resync_successes"] == before + 1
            assert Peer.writes == 0
            Peer.drop_relay_response = True
            await entry.runtime_data.relay_coordinator.async_refresh()
            assert not Peer.drop_relay_response
            assert entry.runtime_data.relay_coordinator.last_update_success
            assert hass.states.get(select).state == "On"
            assert hass.states.get(em).state == "Auto"
            assert Peer.writes == 0
        select_file = directory / "qualification-select.json"
        if phase == "restart":
            assert em == json.loads(select_file.read_text(encoding="utf-8"))
            return
        select_file.write_text(json.dumps(em), encoding="utf-8")
        Peer.presence_lookup = False
        await entry.runtime_data.relay_coordinator.async_refresh()
        assert hass.states.get(em).state == "Auto"
        assert (
            hass.states.get(em).attributes["registration_source"]
            == "activation_and_observed_module"
        )

        async def set_mode(entity_id, option):
            await hass.services.async_call(
                "select",
                "select_option",
                {"entity_id": entity_id, "option": option},
                blocking=True,
            )
            await hass.async_block_till_done()

        # Both actions use the independent peer's 0x0400 persistence path.
        await set_mode(display, "°F")
        assert Peer.saves == 1 and Peer.settings[60] == 1
        assert hass.states.get(display).state == "°F"
        await set_mode(display, "°F")
        assert Peer.saves == 1
        await hass.services.async_call(
            "switch", "turn_off", {"entity_id": daylight}, blocking=True
        )
        assert Peer.saves == 2 and Peer.settings[56] == 0
        assert hass.states.get(daylight).state == "off"
        assert Peer.writes == 0
        Peer.release_offer_after_write = True
        try:
            for option, expected in (("Off", 0), ("On", 4), ("Auto", 2)):
                await set_mode(select, option)
                assert Peer.mode == expected
                assert hass.states.get(select).state == option
                assert hass.states.get(em).state == "Auto"
            for option, expected in (("Min", 1), ("Max", 3), ("Off", 0), ("Auto", 2)):
                await set_mode(em, option)
                assert Peer.em_mode == expected
                assert Peer.mode == 2
                assert hass.states.get(em).state == option
                assert hass.states.get(select).state == "Auto"
            assert Peer.release_offer_count == 7
            assert Peer.redundant_release_commands == 0
        finally:
            Peer.release_offer_after_write = False
        assert Peer.writes == 7
        try:
            await set_mode(em, "On")
        except (HomeAssistantError, ValueError):
            pass
        else:
            raise AssertionError("Unsupported mode accepted")
        assert Peer.writes == 7

        telemetry = entry.runtime_data.coordinator
        initial_inventory = telemetry.observed_inventory
        assert telemetry.observed_inventory is initial_inventory
        assert any(device.address == 0x6651 for device in initial_inventory)
        Peer.em_present = False
        try:
            await set_mode(em, "Off")
        except HomeAssistantError:
            pass
        else:
            raise AssertionError("Missing EM accepted")
        assert Peer.writes == 7
        assert hass.states.get(em).state == "unavailable"
        assert telemetry.observed_inventory is not initial_inventory
        assert all(device.address != 0x6651 for device in telemetry.observed_inventory)
        Peer.em_present = True
        with patch("custom_components.deltasol.load_catalog", return_value=fixture):
            assert await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()
        assert registry.async_get_entity_id("select", "deltasol", EM_UID) == em
        assert hass.states.get(em).state == "Auto"

        Peer.deny = True
        try:
            await set_mode(select, "Off")
        except HomeAssistantError:
            pass
        else:
            raise AssertionError("Authentication failure accepted")
        assert Peer.writes == 7
        assert hass.states.get(select).state == "unavailable"
        assert float(hass.states.get(sensor).state) == 42
        diagnostic = await async_get_config_entry_diagnostics(hass, entry)
        access = diagnostic["parameter_access"]
        assert access["status"] == "unavailable"
        assert access["last_error"] == "authentication"
        assert access["relays"]["R4"]["mode"] is None
        assert access["relays"]["R4"]["last_observed_mode"] == "Auto"
        assert access["relays"]["R4"]["writable_options"] == []
        Peer.deny = False
        with patch("custom_components.deltasol.load_catalog", return_value=fixture):
            assert await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()
        assert registry.async_get_entity_id("select", "deltasol", SELECT_UID) == select
        assert hass.states.get(select).state == "Auto"
        assert float(hass.states.get(sensor).state) == 42
        assert Peer.writes == 7
        assert Peer.saves == 2

        # Exercise real 16-item batches across 200+ independent TCP sessions.
        # Accelerate only pacing here; earlier and later lifecycle tests use
        # production delays and real socket timeout behavior.
        soak_definitions = tuple(
            Definition(f"Soak{n}", 30000 + n, "volatile", None, {}) for n in range(3200)
        )
        soak_lookups = {semantic_hash(f"Soak{n}"): 30000 + n for n in range(0, 3200, 2)}
        soak_values = {30000 + n: n for n in range(0, 3200, 2)}
        # A discovery stress fixture must not overwrite native BX Plus
        # settings, function selectors or timer references. Restore the peer
        # dictionaries after the soak, even when an assertion fails.
        assert not soak_lookups.keys() & Peer.extra_lookups.keys()
        assert not soak_values.keys() & (
            Peer.extra_values.keys() | Peer.settings.keys() | Peer.weekly_words.keys()
        )
        native_lookups = dict(Peer.extra_lookups)
        native_values = dict(Peer.extra_values)
        soak = Catalog("isolated-soak", soak_definitions)
        sessions_before = Peer.sessions
        with (
            patch.dict(Peer.extra_lookups, soak_lookups),
            patch.dict(Peer.extra_values, soak_values),
            patch("custom_components.deltasol.load_catalog", return_value=soak),
            patch("custom_components.deltasol.DISCOVERY_BATCH_GAP_SECONDS", 0.02),
            patch("custom_components.deltasol.parameters.SESSION_GAP_SECONDS", 0.05),
        ):
            Peer.offer_waiting.clear()
            Peer.offer_release.clear()
            Peer.hold_next_offer = True
            Peer.failed_release_sessions = 1
            await hass.services.async_call(
                "button", "press", {"entity_id": button}, blocking=True
            )
            assert await hass.async_add_executor_job(Peer.offer_waiting.wait, 10)
            action = asyncio.create_task(set_mode(select, "Auto"))
            try:
                await asyncio.sleep(0.1)
                assert not action.done()
            finally:
                Peer.offer_release.set()
            await asyncio.wait_for(action, timeout=15)
            capture = entry.runtime_data.capture
            assert capture.offset < 3200
            await entry.runtime_data.capture_task
            assert capture.status == "complete" and capture.offset == 3200
            assert capture.summary()["read_count"] == 1600
            assert capture.summary()["not_resolved_count"] == 1600
            assert Peer.sessions - sessions_before >= 201
            assert capture.retry_count >= 1
            assert capture.last_failure["phase"] == "release"
            assert Peer.writes == 7 and Peer.saves == 2
            assert hass.states.get(select).state == "Auto"
            assert float(hass.states.get(sensor).state) == 42

        assert Peer.extra_lookups == native_lookups
        assert Peer.extra_values == native_values
        assert Peer.extra_values[11266] == 4  # native circulation function
        assert Peer.extra_values[11346] == 2  # allocated timer reference

        # Release loss, late unrelated traffic and repeated acquisition
        # failures are exercised through the actual coordinator, not mocks.
        relay = entry.runtime_data.relay_coordinator
        Peer.release_silence = 1
        await relay.async_refresh()
        assert relay.last_update_success
        Peer.release_other_first = True
        await relay.async_refresh()
        assert relay.last_update_success
        Peer.failed_release_sessions = 1
        Peer.silent_offers = 1
        await relay.async_refresh()
        assert relay.last_update_success
        health = (await async_get_config_entry_diagnostics(hass, entry))[
            "parameter_access"
        ]["transport"]
        assert health["release_failures"] >= 1
        assert health["other_live_packets"] >= 1
        assert health["failure_counts"]["bus_acquisition"] >= 1
        assert health["consecutive_transport_failures"] == 0
        assert Peer.writes == 7 and Peer.saves == 2
        assert float(hass.states.get(sensor).state) == 42

        # A real native action must take its turn before the next Discovery
        # batch. Hold the independent peer's first offer, queue two actions,
        # then allow that batch to finish. No second bus or optimistic state.
        def hold_offer():
            Peer.offer_waiting.clear()
            Peer.offer_release.clear()
            Peer.hold_next_offer = True

        async def start_held_capture():
            hold_offer()
            await hass.services.async_call(
                "button", "press", {"entity_id": button}, blocking=True
            )
            assert await hass.async_add_executor_job(Peer.offer_waiting.wait, 10)
            assert entry.runtime_data.capture.status == "running"
            return entry.runtime_data.capture

        with (
            patch("custom_components.deltasol.load_catalog", return_value=fixture),
            patch("custom_components.deltasol.discovery.BATCH_SIZE", 1),
        ):
            Peer.events.clear()
            active = await start_held_capture()
            relay_action = asyncio.create_task(
                hass.services.async_call(
                    "select",
                    "select_option",
                    {"entity_id": select, "option": "Off"},
                    blocking=True,
                )
            )
            config_action = asyncio.create_task(
                hass.services.async_call(
                    "select",
                    "select_option",
                    {"entity_id": display, "option": "°C"},
                    blocking=True,
                )
            )
            try:
                await asyncio.sleep(0.1)
                assert not relay_action.done() and not config_action.done()
            finally:
                Peer.offer_release.set()
            await asyncio.gather(relay_action, config_action)
            await entry.runtime_data.capture_task
            assert active.status == "complete" and active.offset == 3
            assert Peer.events.index("relay_write") < Peer.events.index("missing")
            assert Peer.events.index("save") < Peer.events.index("missing")
            assert hass.states.get(select).state == "Off"
            assert hass.states.get(display).state == "°C"
            assert Peer.mode == 0 and Peer.settings[60] == 0

            # An intentional pause must survive a reload without auto-start.
            active = await start_held_capture()
            started_at = active.started_at
            try:
                await hass.services.async_call(
                    "button", "press", {"entity_id": button}, blocking=True
                )
            finally:
                Peer.offer_release.set()
            await entry.runtime_data.capture_task
            assert active.status == "paused" and 0 < active.offset < 3
            assert await hass.config_entries.async_reload(entry.entry_id)
            paused = entry.runtime_data.capture
            assert paused.started_at == started_at and paused.offset == active.offset
            assert paused.status == "paused"
            assert entry.runtime_data.capture_task is None
            assert entry.runtime_data.auto_start_task is None
            await hass.services.async_call(
                "button", "press", {"entity_id": button}, blocking=True
            )
            await entry.runtime_data.capture_task
            assert paused.status == "complete" and paused.offset == 3

            # Reload while an actual read is in progress. The committed batch
            # must survive and the same capture must resume, not restart.
            active = await start_held_capture()
            started_at = active.started_at
            reload_task = asyncio.create_task(
                hass.config_entries.async_reload(entry.entry_id)
            )
            try:
                await asyncio.sleep(0.1)
                assert not reload_task.done()
            finally:
                Peer.offer_release.set()
            assert await reload_task
            resumed = entry.runtime_data.capture
            assert resumed.started_at == started_at
            assert resumed.offset >= 1
            if entry.runtime_data.capture_task:
                await entry.runtime_data.capture_task
            assert resumed.status == "complete" and resumed.offset == 3
            assert float(hass.states.get(percent).state) == 100

        # An acquisition failure must recover the native entity without
        # repeating a write. The user sees the failed action and can retry.
        writes_before = Peer.writes
        Peer.drop_next_offer = True
        try:
            await set_mode(select, "Auto")
        except HomeAssistantError:
            pass
        else:
            raise AssertionError("Failed acquisition reported success")
        assert not Peer.drop_next_offer
        assert Peer.writes == writes_before and Peer.mode == 0
        assert hass.states.get(select).state == "Off"
        assert hass.states.get(em).state == "Auto"
        await set_mode(select, "Auto")
        await set_mode(display, "°F")
        assert Peer.mode == 2 and Peer.settings[60] == 1
        assert Peer.writes == writes_before + 1
        assert Peer.saves == 4

        # The previous lifecycle, contention, retry and release tests use
        # production pacing. For the extensive native semantic matrix below,
        # accelerate only idle gaps on the independent loopback peer. Every
        # actual TCP request, reply, save and verification is still executed.
        semantic_pacing.enter_context(
            patch("custom_components.deltasol.parameters.SESSION_GAP_SECONDS", 0.05)
        )
        semantic_pacing.enter_context(
            patch("custom_components.deltasol.DISCOVERY_BATCH_GAP_SECONDS", 0.02)
        )
        print("ISOLATED_HA native semantic matrix started", flush=True)
        from time import monotonic, process_time

        matrix_start = monotonic()
        cpu_start = process_time()

        def matrix_checkpoint(label):
            health = entry.runtime_data.coordinator.api.parameter_transport_health()
            phases = health["phase_timings"]
            session_total = phases["session"]["total_seconds"]
            cooldown_total = phases["cooldown_wait"]["total_seconds"]
            print(
                f"ISOLATED_HA matrix {label}: "
                f"{monotonic() - matrix_start:.1f}s "
                f"cpu={process_time() - cpu_start:.1f}s "
                f"session_total={session_total:.1f}s cooldown_total={cooldown_total:.1f}s "
                f"sessions={Peer.sessions} reads={Peer.reads} "
                f"lookups={Peer.lookups} writes={Peer.writes} saves={Peer.saves}",
                flush=True,
            )

        # Discover the actual installed-function selectors through TCP, not
        # direct runtime injection. Then operate native Number/Select/Switch
        # entities and check both wire persistence and HA observations.
        archive = directory / "deltasol" / "BXPLUS_2.08.zip"
        archive.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile("/reference/BXPLUS_2.08.zip", archive)
        # Reproduce the field regression with the existing private checkpoint
        # from the earlier fixture, whose catalogue identity is incompatible
        # with the actual verified archive. Reload through native HA.
        before = (Peer.writes, Peer.saves)
        assert await hass.config_entries.async_reload(entry.entry_id)
        runtime = entry.runtime_data
        assert runtime.capture is None
        assert runtime.checkpoint_error == "checkpoint_incompatible"
        assert runtime.rejected_capture is not None
        assert runtime.relay_coordinator._configuration_catalog().matches(
            ADDRESS, CHANGESET
        )
        for _ in range(32):
            await runtime.relay_coordinator.async_refresh()
            await hass.async_block_till_done()
            if any(
                cap.setting.name.startswith("Anlage_Wf1_")
                for cap in runtime.relay_coordinator.data.configuration.values()
            ):
                break
        else:
            raise AssertionError(
                "Native function not recovered without historical capture"
            )
        assert runtime.capture is None
        assert (Peer.writes, Peer.saves) == before
        print("ISOLATED_HA independent live capability recovery PASS", flush=True)
        from homeassistant.auth.const import (
            GROUP_ID_ADMIN,
            GROUP_ID_READ_ONLY,
            GROUP_ID_USER,
        )
        from homeassistant.components.frontend import DATA_PANELS

        from custom_components.deltasol.dashboard import (
            dashboard_objects,
            scoped_control,
        )

        admin = await hass.auth.async_create_user(
            "Qualification administrator", group_ids=[GROUP_ID_ADMIN]
        )
        resident = await hass.auth.async_create_user(
            "Qualification resident", group_ids=[GROUP_ID_USER]
        )
        readonly = await hass.auth.async_create_user(
            "Qualification reader", group_ids=[GROUP_ID_READ_ONLY]
        )
        assert not resident.is_admin and not readonly.is_admin
        assert not dashboard_objects(hass, admin)
        assert "resol" not in hass.data.get(DATA_PANELS, {})
        options = hass.config_entries.options
        result = await options.async_init(entry.entry_id)
        result = await options.async_configure(
            result["flow_id"],
            {
                "vbus_password": "local-test",
                "enable_dashboard": True,
                "show_engineering": True,
            },
        )
        assert result["type"] == "create_entry"
        await hass.async_block_till_done()
        assert entry.state == ConfigEntryState.LOADED
        assert "resol" in hass.data[DATA_PANELS]
        (admin_view,) = dashboard_objects(hass, admin)
        (resident_view,) = dashboard_objects(hass, resident)
        (reader_view,) = dashboard_objects(hass, readonly)
        assert admin_view["engineering"] and admin_view["settings"]
        assert not resident_view["engineering"] and not resident_view["settings"]
        assert not reader_view["engineering"]
        assert all(
            not row["control"]
            for asset in reader_view["objects"]
            for row in asset["capabilities"]
        )
        assert any(
            row["entity_id"] == sensor
            for asset in resident_view["objects"]
            for row in asset["capabilities"]
        )
        assert scoped_control(hass, admin, entry.entry_id, select)["control"]
        second = ConfigEntry(
            version=entry.version,
            minor_version=entry.minor_version,
            domain="deltasol",
            title="Second installation",
            data={**entry.data, "port": 18081},
            source="user",
            unique_id="isolated-secondary",
            options={"enable_dashboard": True, "show_engineering": False},
            discovery_keys=MappingProxyType({}),
            subentries_data=None,
        )
        await hass.config_entries.async_add(second)
        await hass.async_block_till_done()
        assert second.state == ConfigEntryState.LOADED
        second_sensor = registry.async_get_entity_id(
            "sensor", "deltasol", f"isolated-secondary_{SENSOR_UID}"
        )
        assert second_sensor and second_sensor != sensor
        assert registry.async_get(second_sensor).config_entry_id == second.entry_id
        assert float(hass.states.get(second_sensor).state) == 42
        views = {view["id"]: view for view in dashboard_objects(hass, admin)}
        assert set(views) == {entry.entry_id, second.entry_id}
        assert views[entry.entry_id]["engineering"]
        assert not views[second.entry_id]["engineering"]
        assert "resol" in hass.data[DATA_PANELS]
        assert await hass.config_entries.async_remove(second.entry_id)
        assert "resol" in hass.data[DATA_PANELS]
        assert len(dashboard_objects(hass, admin)) == 1
        assert registry.async_get(sensor).config_entry_id == entry.entry_id
        for user, ident in ((resident, entry.entry_id), (admin, "other-entry")):
            try:
                scoped_control(hass, user, ident, select)
            except HomeAssistantError:
                pass
            else:
                raise AssertionError("Engineering or foreign entry control leaked")
        from aiohttp import ClientSession

        async def dashboard_request(user, command, **payload):
            token = await hass.auth.async_create_refresh_token(
                user, client_id="http://127.0.0.1:18123/"
            )
            access_token = hass.auth.async_create_access_token(token)
            async with (
                ClientSession() as session,
                session.ws_connect("http://127.0.0.1:18123/api/websocket") as ws,
            ):
                assert (await ws.receive_json())["type"] == "auth_required"
                await ws.send_json({"type": "auth", "access_token": access_token})
                assert (await ws.receive_json())["type"] == "auth_ok"
                await ws.send_json({"id": 1, "type": command, **payload})
                response = await ws.receive_json()
                assert response["id"] == 1
                return response

        # A resident subscription receives native coordinator updates without
        # another VBus session or registry mutation.
        token = await hass.auth.async_create_refresh_token(
            resident, client_id="http://127.0.0.1:18123/"
        )
        access_token = hass.auth.async_create_access_token(token)
        async with (
            ClientSession() as session,
            session.ws_connect("http://127.0.0.1:18123/api/websocket") as ws,
        ):
            assert (await ws.receive_json())["type"] == "auth_required"
            await ws.send_json({"type": "auth", "access_token": access_token})
            assert (await ws.receive_json())["type"] == "auth_ok"
            sessions_before = Peer.sessions
            await ws.send_json({"id": 1, "type": "deltasol/dashboard/subscribe"})
            assert (await ws.receive_json())["success"]
            initial = (await ws.receive_json())["event"][0]
            assert not initial["engineering"]
            assert initial["health"]["parameter_current"]
            relay = entry.runtime_data.relay_coordinator
            relay.async_set_update_error(HomeAssistantError("qualification outage"))
            changed = (await ws.receive_json(timeout=5))["event"][0]
            assert not changed["health"]["parameter_current"]
            relay.async_set_updated_data(relay.data)
            recovered = (await ws.receive_json(timeout=5))["event"][0]
            assert recovered["health"]["parameter_current"]
            assert Peer.sessions == sessions_before
            await ws.send_json(
                {"id": 2, "type": "unsubscribe_events", "subscription": 1}
            )
            assert (await ws.receive_json())["success"]

        # Authenticate against the real HA websocket and exercise registration,
        # schemas, permissions and the native action context, not just wrappers.
        listed = await dashboard_request(admin, "deltasol/dashboard/list")
        assert listed["success"]
        assert listed["result"][0]["id"] == entry.entry_id
        report = await dashboard_request(
            admin, "deltasol/dashboard/engineering", entry_id=entry.entry_id
        )
        assert report["success"]
        assert report["result"]["operational_contracts_source"]["changeset"] == (
            f"{CHANGESET:08X}"
        )
        assert "Schema" in report["result"]["reference_inventory"]
        denied = await dashboard_request(
            resident, "deltasol/dashboard/engineering", entry_id=entry.entry_id
        )
        assert not denied["success"]
        response = await dashboard_request(
            admin,
            "deltasol/dashboard/action",
            entry_id=entry.entry_id,
            entity_id=select,
            value=hass.states.get(select).state,
        )
        assert response["success"]
        assert response["result"] == {
            "outcome": "observed",
            "value": hass.states.get(select).state,
        }
        response = await dashboard_request(
            resident,
            "deltasol/dashboard/action",
            entry_id=entry.entry_id,
            entity_id=select,
            value="Off",
        )
        assert not response["success"]
        assert (Peer.writes, Peer.saves) == before
        # Disable using the native options flow, verify single panel removal.
        result = await options.async_init(entry.entry_id)
        result = await options.async_configure(
            result["flow_id"],
            {
                "vbus_password": "local-test",
                "enable_dashboard": False,
                "show_engineering": False,
            },
        )
        assert result["type"] == "create_entry"
        await hass.async_block_till_done()
        assert entry.state == ConfigEntryState.LOADED
        assert "resol" not in hass.data.get(DATA_PANELS, {})
        assert not dashboard_objects(hass, admin)
        assert (Peer.writes, Peer.saves) == before
        print("ISOLATED_HA native dashboard lifecycle and role scope PASS", flush=True)
        result = await options.async_init(entry.entry_id)
        result = await options.async_configure(
            result["flow_id"],
            {
                "vbus_password": "local-test",
                "enable_dashboard": True,
                "show_engineering": True,
            },
        )
        assert result["type"] == "create_entry"
        await hass.async_block_till_done()
        await hass.services.async_call(
            "button", "press", {"entity_id": button}, blocking=True
        )
        await entry.runtime_data.capture_task
        capture = entry.runtime_data.capture
        assert capture.offset == expected_config_count
        assert {d.name for d in capture.candidates} == qualification_names
        assert capture.catalog.matches(ADDRESS, CHANGESET)
        assert len(capture.catalog.definitions) == 15680
        await entry.runtime_data.relay_coordinator.async_refresh()
        await hass.async_block_till_done()
        diagnostic = await async_get_config_entry_diagnostics(hass, entry)
        assert (
            diagnostic["configuration_discovery"]["catalogue_source"]["compatibility"]
            == "verified"
        )
        assert_weekly_evidence(diagnostic)
        bound = entry.runtime_data.relay_coordinator.data.configuration
        assert any(cap.setting.value_definition is not None for cap in bound.values())
        day = "Wsu1_Schaltuhr_Woche_TagMo_Daten"
        contract = diagnostic["configuration_discovery"]["parameters"][day]
        assert contract["reference_contract"]["catalogue_binding"] == "verified"
        assert contract["contract_status"] == "live_verified"
        control = diagnostic["configuration_controls"][day]
        assert control["weekly_program"] == 1
        assert control["active_references"] == ["Anlage_Wf4_SchaltuhrNr"]
        # A bound, unallocated day is a known contract, not a live control.
        inactive = diagnostic["configuration_discovery"]["parameters"][
            "Wsu2_Schaltuhr_Woche_TagMo_Daten"
        ]
        assert inactive["reference_contract"]["catalogue_binding"] == "verified"
        assert inactive["write_access_reason"] == "live_capability_not_confirmed"
        assert (
            diagnostic["configuration_discovery"]["parameters"][
                "Wsu0_Schaltuhr_Woche_TagMo_Daten"
            ]["write_access_reason"]
            == "reserved_native_weekly_slot"
        )

        def entity(name, kind):
            return registry.async_get_entity_id(
                kind, "deltasol", f"isolated-gateway_7112_{name}_configuration"
            )

        # Read-only native commissioning choices must remain visible in the
        # actual discovery snapshot, with their exact reason and no write
        # entity. Existing operational controls are exercised below.
        for name, reason in (
            (
                "Heizung_Heizkreis1_Raumthermostat1_Option",
                "native_registration_clears_assignment",
            ),
            (
                "Heizung_Heizkreis1_ONachheizung",
                "native_registration_clears_assignment",
            ),
            (
                "Heizung_Wf2_ThBwErwaermung_OLadepumpe",
                "native_registration_clears_assignment",
            ),
            (
                "Heizung_Heizkreis1_OFernzugriff",
                "native_input_source_configuration",
            ),
            (
                "Heizung_Heizkreis1_Fernzugriff_Modus",
                "native_input_source_configuration",
            ),
            (
                "Heizung_RelaisAnforderung1_ORelais1",
                "native_output_assignment_configuration",
            ),
            (
                "Heizung_RelaisAnforderung1_Aktiviert_Modus",
                "native_function_registry_membership",
            ),
            (
                "Anlage_WTUmwaelzpumpe_Aktiviert_Modus",
                "native_menu_permanently_hidden",
            ),
        ):
            item = diagnostic["configuration_discovery"]["parameters"][name]
            assert item["write_access"] != "profile_verified"
            assert item["write_access_reason"] == reason
            assert all(
                entity(name, kind) is None for kind in ("number", "select", "switch")
            )

        async def number(name, value):
            target = entity(name, "number")
            assert target
            await hass.services.async_call(
                "number",
                "set_value",
                {"entity_id": target, "value": value},
                blocking=True,
            )
            assert float(hass.states.get(target).state) == value

        await number("Anlage_Wf1_Mischer_dtIntervall", 5)
        mixer_device = registry.async_get(
            entity("Anlage_Wf1_Mischer_dtIntervall", "number")
        ).device_id
        assert mixer_device != registry.async_get(sensor).device_id
        from homeassistant.helpers import device_registry as dr

        mixer = dr.async_get(hass).async_get(mixer_device)
        assert mixer.via_device_id == registry.async_get(sensor).device_id
        assert mixer.name == "RESOL Arrangement function 1"
        await number("Heizung_Heizkreis1_Heizkurve", 1.5)
        alternative = entity("Wmz1_OAlternativAnzeige", "switch")
        assert alternative and hass.states.get(alternative).state == "off"
        assert (
            registry.async_get(alternative).device_id
            == registry.async_get(hqm_sensor).device_id
        )
        await hass.services.async_call(
            "switch", "turn_on", {"entity_id": alternative}, blocking=True
        )
        await hass.async_block_till_done()
        assert hass.states.get(alternative).state == "on"
        await set_mode(entity("Wmz1_AlternativAnzeigeEinheit", "select"), "Gas")
        await number("Wmz1_AlternativAnzeigeFaktor", 0.5000001)
        await number("Relais_Modul1_R1_MinDrehzahl", 40)
        await number("Relais_Modul1_R1_MaxDrehzahl", 70)
        assert Peer.saves == 11
        await number("Relais_Modul1_R1_MinDrehzahl", 40)
        assert Peer.saves == 11
        try:
            await number("Relais_Modul1_R1_MinDrehzahl", 71)
        except HomeAssistantError:
            pass
        else:
            raise AssertionError("Invalid pump pair accepted")
        assert Peer.saves == 11 and Peer.settings[2170] == 40
        pump = entity("Relais_Modul1_R1_MinDrehzahl", "number")
        assert (
            registry.async_get(pump).device_id
            == registry.async_get(em_sensor).device_id
        )
        await set_mode(em, "Off")
        assert hass.states.get(pump).state == "unavailable"
        await set_mode(em, "Auto")
        assert float(hass.states.get(pump).state) == 40
        assert Peer.saves == 11
        assert float(hass.states.get(sensor).state) == 42

        # The native Number remains the same entity as the active display
        # unit changes. Write the parent only and verify reconnect/persistence.
        name = "Anlage_Wf2_Feststoffkessel_dTEin"
        room_delta = entity("Heizung_Heizkreis1_Raumthermostat1_dTRaum", "number")
        assert room_delta and float(hass.states.get(room_delta).state) == 2
        compound = entity(name, "number")
        assert compound
        assert float(hass.states.get(compound).state) == 10
        await number(name, 12)
        assert Peer.settings[6914] == 60 and Peer.saves == 12
        await set_mode(display, "°C")
        assert entity(name, "number") == compound
        assert float(hass.states.get(compound).state) == 6
        assert float(hass.states.get(room_delta).state) == 1
        assert hass.states.get(compound).attributes["unit_of_measurement"] == "K"
        await number(name, 7)
        assert Peer.settings[6914] == 70 and Peer.saves == 14
        await set_mode(display, "°F")
        assert entity(name, "number") == compound
        assert float(hass.states.get(compound).state) == 14
        assert float(hass.states.get(room_delta).state) == 2
        assert hass.states.get(compound).attributes["unit_of_measurement"] == "°R"
        assert Peer.saves == 15

        # The local relay has no resolved volatile role. Native speed control
        # and exact Auto/registration/readback still authorize its own bounds.
        await number("Relais_Regler_R1_MinDrehzahl", 40)
        await number("Relais_Regler_R1_MaxDrehzahl", 70)
        assert Peer.settings[1974] == 40 and Peer.settings[1975] == 70
        assert Peer.saves == 17
        await set_mode(display, "°C")
        minimum = "Anlage_Wf2_Feststoffkessel_TMin"
        maximum = "Anlage_Wf2_Feststoffkessel_TMax"
        minimum_entity = entity(minimum, "number")
        assert float(hass.states.get(minimum_entity).state) == 60
        await number(minimum, 70)
        await number(maximum, 90)
        assert Peer.settings[6926] == 700 and Peer.settings[6923] == 900
        await set_mode(display, "°F")
        assert entity(minimum, "number") == minimum_entity
        assert float(hass.states.get(minimum_entity).state) == 158
        assert float(hass.states.get(entity(maximum, "number")).state) == 194
        assert Peer.saves == 21

        # Solar settings require the observed native store, collector and output.
        # Removing a native assignment cannot leave a writable stale control.
        solar_name = "Solar_Sp1_dTein"
        solar = entity(solar_name, "number")
        assert solar and float(hass.states.get(solar).state) == 12
        await number(solar_name, 14)
        assert Peer.settings[3579] == 70 and Peer.saves == 22
        Peer.extra_values[3452] = 0
        await entry.runtime_data.relay_coordinator.async_refresh()
        assert hass.states.get(solar).state == "unavailable"
        Peer.extra_values[3452] = 1
        await entry.runtime_data.relay_coordinator.async_refresh()
        assert entity(solar_name, "number") == solar
        assert float(hass.states.get(solar).state) == 14

        # Thermally controlled circulation has its own selected-unit pair.
        # Switching to timer-only removes the temperature controls.
        name = "Heizung_Wf1_Zirkulation_TEin"
        circulation = entity(name, "number")
        assert circulation and float(hass.states.get(circulation).state) == 104
        await number(name, 122)
        assert Peer.settings[11408] == 500 and Peer.saves == 23
        Peer.extra_values[11405] = 1
        await entry.runtime_data.relay_coordinator.async_refresh()
        assert hass.states.get(circulation).state == "unavailable"
        Peer.extra_values[11405] = 0
        await entry.runtime_data.relay_coordinator.async_refresh()
        assert entity(name, "number") == circulation
        assert float(hass.states.get(circulation).state) == 122

        # Native solar sequence settings remain tied to live topology/flags.
        for name, value in (
            ("Solar_Pendelpause", 3),
            ("Solar_Umwaelzzeit", 20),
            ("Solar_Pausendrehzahl", 40),
            ("Solar_Pumpenverzoegerung", 25),
        ):
            await number(name, value)
        assert Peer.saves == 27
        sequence = entity("Solar_Pendelpause", "number")
        Peer.extra_values[3612] = 0
        await entry.runtime_data.relay_coordinator.async_refresh()
        assert hass.states.get(sequence).state == "unavailable"
        Peer.extra_values[3612] = 3
        await entry.runtime_data.relay_coordinator.async_refresh()
        assert entity("Solar_Pendelpause", "number") == sequence
        assert float(hass.states.get(sequence).state) == 3
        assert Peer.saves == 27

        # Return mixing uses the selected-unit parent and live assignments.
        mixing = "Anlage_Wf3_RLBeimischung_dTEin"
        target = entity(mixing, "number")
        assert target and float(hass.states.get(target).state) == 10
        await number(mixing, 11)
        assert Peer.settings[7261] == 55 and Peer.saves == 28
        try:
            await number(mixing, 6)
        except HomeAssistantError:
            pass
        else:
            raise AssertionError("Invalid return mixing pair accepted")
        Peer.extra_values[7102] = 0
        await entry.runtime_data.relay_coordinator.async_refresh()
        assert hass.states.get(target).state == "unavailable"
        Peer.extra_values[7102] = 2
        await entry.runtime_data.relay_coordinator.async_refresh()
        assert entity(mixing, "number") == target
        assert float(hass.states.get(target).state) == 11
        assert Peer.saves == 28
        for suffix, value in (
            ("dTSoll", -20),
            ("Mischerlaufzeit", 120),
            ("Mischerstellzeit", 5),
        ):
            await number(f"Anlage_Wf3_RLBeimischung_{suffix}", value)
        assert Peer.settings[7267] == -100 and Peer.settings[7273] == 120
        assert Peer.settings[7274] == 5 and Peer.saves == 31

        # Dedicated solar functions have their own assignments, not the
        # generic optional Type slot. All actions use native Number services.
        bypass = "Solar_Wf1_Bypass_dTein"
        target = entity(bypass, "number")
        assert target and float(hass.states.get(target).state) == 12
        await number(bypass, 14)
        Peer.extra_values[3550] = 0
        await entry.runtime_data.relay_coordinator.async_refresh()
        assert hass.states.get(target).state == "unavailable"
        Peer.extra_values[3550] = 5
        await entry.runtime_data.relay_coordinator.async_refresh()
        assert entity(bypass, "number") == target
        assert float(hass.states.get(target).state) == 14
        for name, value in (
            ("Solar_Wf1_CsBypass_Einstrahlung", 250),
            ("Solar_Wf1_CsBypass_Verzoegerung", 150),
            ("Solar_Wf1_Zieltemperatur_Zieltemperatur", 158),
            ("Solar_Wf1_Zieltemperatur_Anstieg", 6),
        ):
            await number(name, value)
        assert Peer.settings[3908] == 70 and Peer.settings[3991] == 250
        assert Peer.settings[3992] == 150 and Peer.settings[4344] == 700
        assert Peer.settings[4347] == 30 and Peer.saves == 36

        # Independently exercise both newly added native Solar families.
        tube = "Solar_Wf1_Roehrenkollektor"
        exchanger = "Solar_Wf1_ExtWT"
        for suffix, value in (
            ("tBeginn", 390),
            ("tEnde", 450),
            ("Laufzeit", 40),
            ("Pausezeit", 12),
        ):
            await number(tube + "_" + suffix, value)
        for suffix, value in (
            ("Nachlaufzeit", 4),
            ("Mindestlaufzeit", 5),
            ("dTein", 14),
            ("dTaus", 10),
            ("ZieltemperaturSekundaer", 140),
            ("dTAnstieg", 6),
        ):
            await number(exchanger + "_" + suffix, value)
        assert Peer.saves == 46
        for name, value in ((tube + "_tBeginn", 450), (exchanger + "_dTein", 10)):
            try:
                await number(name, value)
            except HomeAssistantError:
                pass
            else:
                raise AssertionError("Invalid Solar pair accepted")
        assert Peer.saves == 46
        tube_entity = entity(tube + "_tBeginn", "number")
        exchanger_entity = entity(exchanger + "_dTein", "number")
        Peer.extra_values[3416] = 10101
        await entry.runtime_data.relay_coordinator.async_refresh()
        assert hass.states.get(tube_entity).state == "unavailable"
        assert hass.states.get(exchanger_entity).state == "unavailable"
        Peer.extra_values[3416] = 20201
        await entry.runtime_data.relay_coordinator.async_refresh()
        assert entity(tube + "_tBeginn", "number") == tube_entity
        assert entity(exchanger + "_dTein", "number") == exchanger_entity
        assert float(hass.states.get(tube_entity).state) == 390
        assert float(hass.states.get(exchanger_entity).state) == 14

        before_comfort = Peer.saves
        for suffix, value in (
            ("TSommerTag", 86),
            ("TSommerNacht", 50),
            ("tSommerTagEin", 555),
            ("tSommerTagAus", 1275),
            ("Raumthermostat1_dTAbsenkung", 12),
        ):
            await number("Heizung_Heizkreis1_" + suffix, value)
        assert Peer.saves == before_comfort + 5
        day_clock = entity("Heizung_Heizkreis1_tSommerTagEin", "number")
        Peer.settings[13740] = 0
        await entry.runtime_data.relay_coordinator.async_refresh()
        assert hass.states.get(day_clock).state == "unavailable"
        Peer.settings[13740] = 1
        await entry.runtime_data.relay_coordinator.async_refresh()
        assert entity("Heizung_Heizkreis1_tSommerTagEin", "number") == day_clock
        assert float(hass.states.get(day_clock).state) == 555

        before_operating = Peer.saves
        for name, value in (
            ("Heizung_Heizkreis1_Nachheizung_dTEin", -9),
            ("Heizung_Heizkreis1_Nachheizung_dTAus", 12),
            ("Heizung_Heizkreis1_Nachheizung_tFruehstart", 90),
            ("Heizung_Heizkreis1_Nachheizung_tBlockierung", 45),
            ("Heizung_Heizkreis1_Kuehlung_Timer_tEin", 555),
            ("Heizung_Heizkreis1_Kuehlung_Timer_tAus", 1215),
            ("Anlage_WTUmwaelzpumpe_SollDrehzahl", 50),
            ("Anlage_WTUmwaelzpumpe_Nachlaufzeit", 30),
            ("Anlage_WTUmwaelzpumpe_RuhemodusDauer", 12.5),
            ("Heizung_RelaisPumpe1_Anlauf_dtAnlauf", 75),
            ("Heizung_RelaisPumpe1_Nachlauf_TNachlauf", 140),
        ):
            await number(name, value)
        assert Peer.saves == before_operating + 11
        for name, value in (
            ("Heizung_Heizkreis1_Nachheizung_dTEin", 12),
            ("Heizung_Heizkreis1_Kuehlung_Timer_tEin", 556),
            ("Anlage_WTUmwaelzpumpe_RuhemodusDauer", 12.6),
        ):
            try:
                await number(name, value)
            except HomeAssistantError:
                pass
            else:
                raise AssertionError("Invalid operating value accepted")
        assert Peer.saves == before_operating + 11
        early = entity("Heizung_Heizkreis1_Nachheizung_tFruehstart", "number")
        Peer.settings[13714] = 0
        await entry.runtime_data.relay_coordinator.async_refresh()
        assert hass.states.get(early).state == "unavailable"
        Peer.settings[13714] = 1
        await entry.runtime_data.relay_coordinator.async_refresh()
        assert float(hass.states.get(early).state) == 90
        assert entity("Heizung_Heizkreis1_Nachheizung_tFruehstart", "number") == early

        pump = entity("Heizung_RelaisPumpe1_Nachlauf_TNachlauf", "number")
        Peer.extra_values[11189] = 0
        await entry.runtime_data.relay_coordinator.async_refresh()
        assert hass.states.get(pump).state == "unavailable"
        Peer.extra_values[11189] = 6
        await entry.runtime_data.relay_coordinator.async_refresh()
        assert float(hass.states.get(pump).state) == 140

        before_native = Peer.saves
        for name, value in (
            ("Heizung_Heizkreis1_MischerIntervall", 6),
            ("Heizung_Heizkreis1_Mischer_dTEin", 4),
            ("Heizung_Heizkreis1_Mischer_dTAus", 2.4),
            ("Heizung_RelaisAnforderung1_Mindestlaufzeit_tMin", 15),
            ("Anlage_Wf5_Funktionsblock_dtMindestausschaltzeit", 75),
        ):
            await number(name, value)
        assert Peer.saves == before_native + 5
        for name, value in (
            ("Heizung_Heizkreis1_Mischer_dTEin", 3.2),
            ("Heizung_RelaisAnforderung1_Mindestlaufzeit_tMin", 16),
        ):
            try:
                await number(name, value)
            except HomeAssistantError:
                pass
            else:
                raise AssertionError("Invalid native timing/mixer value accepted")
        assert Peer.saves == before_native + 5
        mixer = entity("Heizung_Heizkreis1_MischerIntervall", "number")
        Peer.extra_values[13780] = 0
        await entry.runtime_data.relay_coordinator.async_refresh()
        assert hass.states.get(mixer).state == "unavailable"
        Peer.extra_values[13780] = 3
        await entry.runtime_data.relay_coordinator.async_refresh()
        assert float(hass.states.get(mixer).state) == 6

        before_curve = Peer.saves
        root = "Heizung_Heizkreis1_Heizsystem_"
        Peer.extra_values[13978] = 3
        await entry.runtime_data.relay_coordinator.async_refresh()
        await hass.async_block_till_done()
        for suffix, value in (
            ("Gerade_TAussen1", 32),
            ("Gerade_TAussen2", 32),
            ("Gerade_TVorlauf1", 122),
            ("Gerade_TVorlauf2", 158),
        ):
            await number(root + suffix, value)
        assert Peer.saves == before_curve + 4
        Peer.extra_values[13978] = 4
        await entry.runtime_data.relay_coordinator.async_refresh()
        await hass.async_block_till_done()
        await number(root + "Raum_TStart", 122)
        aggregation = entity(root + "Raumeinfluss_Abweichung", "select")
        assert aggregation
        await set_mode(aggregation, "Maximum")
        assert Peer.saves == before_curve + 6
        Peer.extra_values[13978] = 5
        await entry.runtime_data.relay_coordinator.async_refresh()
        await hass.async_block_till_done()
        await number(root + "Raumeinfluss_Faktor", 7)
        assert Peer.saves == before_curve + 7
        Peer.extra_values[13834] = 0
        await entry.runtime_data.relay_coordinator.async_refresh()
        assert hass.states.get(aggregation).state == "unavailable"
        Peer.extra_values[13834] = 4
        Peer.extra_values[13978] = 1
        await entry.runtime_data.relay_coordinator.async_refresh()

        before_cooling = Peer.saves
        await number("Anlage_Wf1_Mischer_TZiel", 158)
        await number("SDKarte_Logging_Intervall", 120)
        logging_method = entity("SDKarte_Logging_Methode", "select")
        await set_mode(logging_method, "Cyclic (overwrite oldest)")
        invert = entity("Display_Invertiert", "switch")
        await hass.services.async_call(
            "switch", "turn_on", {"entity_id": invert}, blocking=True
        )
        cooling = "Heizung_Heizkreis1_Kuehlung_"
        await number(cooling + "Aussen_TAussen", 86)
        await number(cooling + "Kuehlsystem_TVorlauf", 50)
        assert Peer.saves == before_cooling + 6
        Peer.extra_values[14075] = 1
        await entry.runtime_data.relay_coordinator.async_refresh()
        await hass.async_block_till_done()
        for suffix, value in (
            ("TAussen1", 68),
            ("TVorlauf1", 50),
            ("TAussen2", 86),
            ("TVorlauf2", 68),
        ):
            await number(cooling + "Kuehlsystem_" + suffix, value)
        assert Peer.saves == before_cooling + 10
        constant = entity(cooling + "Kuehlsystem_TVorlauf", "number")
        assert hass.states.get(constant).state == "unavailable"
        Peer.extra_values[14075] = 0
        Peer.extra_values[18231] = 1
        await entry.runtime_data.relay_coordinator.async_refresh()
        assert hass.states.get(logging_method).state == "unavailable"
        Peer.extra_values[18231] = 0
        await entry.runtime_data.relay_coordinator.async_refresh()
        assert hass.states.get(logging_method).state == "Cyclic (overwrite oldest)"
        assert float(hass.states.get(constant).state) == 50

        matrix_checkpoint("thermal/cooling PASS")
        before_priority = Peer.saves
        await number("Solar_Sp1_Vorrang", 2)
        await number("Anlage_Wf6_Waermeaustausch_HMinMax", 5)
        assert Peer.saves == before_priority + 2
        try:
            await number("Solar_Sp1_Vorrang", 3)
        except HomeAssistantError:
            pass
        else:
            raise AssertionError("Priority exceeds actual native store count")
        assert Peer.saves == before_priority + 2
        Peer.extra_values[7835] = 0
        await entry.runtime_data.relay_coordinator.async_refresh()
        assert (
            hass.states.get(
                entity("Anlage_Wf6_Waermeaustausch_HMinMax", "number")
            ).state
            == "unavailable"
        )
        Peer.extra_values[7835] = 2
        await entry.runtime_data.relay_coordinator.async_refresh()
        assert (
            float(
                hass.states.get(
                    entity("Anlage_Wf6_Waermeaustausch_HMinMax", "number")
                ).state
            )
            == 5
        )

        before_analogue = Peer.saves
        root = "Heizung_RelaisAnforderung1_Ausgang_"
        for suffix, value in (
            ("dTSollVorlauf", 14),
            ("Vorlauf_dTHysterese", 3),
            ("Vorlauf_dVKorrektur", 0.2),
            ("Vorlauf_dtIntervall", 35),
        ):
            await number(root + suffix, value)
        assert Peer.saves == before_analogue + 4
        interval = entity(root + "Vorlauf_dtIntervall", "number")
        Peer.extra_values[10775] = 0
        await entry.runtime_data.relay_coordinator.async_refresh()
        assert hass.states.get(interval).state == "unavailable"
        Peer.extra_values[10775] = 2
        await entry.runtime_data.relay_coordinator.async_refresh()
        assert float(hass.states.get(interval).state) == 35

        matrix_checkpoint("priority/analogue PASS")
        before_spread = Peer.saves
        name = "Solar_Spreizladung_dTDifferenz"
        await number(name, 70)
        assert Peer.saves == before_spread + 1
        spread = entity(name, "number")
        Peer.extra_values[3439] = 0
        await entry.runtime_data.relay_coordinator.async_refresh()
        assert hass.states.get(spread).state == "unavailable"
        # HA may skip a service on an already unavailable entity without
        # raising. Test the service's observable no-op separately from the
        # integration owner's mandatory rejection of an unavailable command.
        before_rejected = (Peer.writes, Peer.saves, Peer.settings[3425])
        try:
            await hass.services.async_call(
                "number",
                "set_value",
                {"entity_id": spread, "value": 80},
                blocking=True,
            )
        except HomeAssistantError:
            pass
        assert hass.states.get(spread).state == "unavailable"
        assert (Peer.writes, Peer.saves, Peer.settings[3425]) == before_rejected
        uid = registry.async_get(spread).unique_id
        try:
            await entry.runtime_data.relay_coordinator.async_set_configuration(uid, 80)
        except HomeAssistantError as error:
            assert "unavailable" in str(error)
        else:
            raise AssertionError("Unavailable native parallel loading accepted")
        assert (Peer.writes, Peer.saves, Peer.settings[3425]) == before_rejected
        assert Peer.saves == before_spread + 1
        Peer.extra_values[3439] = 1
        await entry.runtime_data.relay_coordinator.async_refresh()
        assert float(hass.states.get(spread).state) == 70

        matrix_checkpoint("spread eligibility PASS")
        # The existing mask is a function number, not the arrangement slot.
        for name, index in (
            ("Heizung_Wf2_OFeststoffkesselAus", 11436),
            ("Heizung_Heizkreis1_Nachheizung_OFeststoffkesselAus", 14037),
        ):
            target = entity(name, "switch")
            before = Peer.saves
            await hass.services.async_call(
                "switch", "turn_on", {"entity_id": target}, blocking=True
            )
            assert Peer.settings[index] == 1
            assert Peer.saves == before + 1
            await hass.services.async_call(
                "switch", "turn_off", {"entity_id": target}, blocking=True
            )
            assert Peer.settings[index] == 0
            assert Peer.saves == before + 2

        # Native flags use the same persistent owner as their companion Numbers.
        for flag, index, companion in (
            (
                "Anlage_Wf5_Funktionsblock_OThermostat1",
                7709,
                "Anlage_Wf5_Funktionsblock_TTh1Ein",
            ),
            (
                "Anlage_Wf5_Funktionsblock_OThermostat2",
                7710,
                "Anlage_Wf5_Funktionsblock_TTh2Ein",
            ),
            (
                "Anlage_Wf5_Funktionsblock_ODifferenz",
                7708,
                "Anlage_Wf5_Funktionsblock_dTEin",
            ),
            (
                "Anlage_Wf2_Feststoffkessel_OZieltemperatur",
                6932,
                "Anlage_Wf2_Feststoffkessel_Zieltemperatur_TZiel",
            ),
            (
                "Heizung_Wf3_BwVorerwaermung_ODifferenz",
                11700,
                "Heizung_Wf3_BwVorerwaermung_dTEin",
            ),
            ("Solar_OPausendrehzahl", 3420, "Solar_Pausendrehzahl"),
            ("Solar_OPumpenverzoegerung", 3422, "Solar_Pumpenverzoegerung"),
            ("Solar_OSpreizladung", 3424, "Solar_Spreizladung_dTDifferenz"),
            (
                "Anlage_Wf7_Ruecklaufanhebung_OSommerAus",
                8200,
                "Anlage_Wf7_Ruecklaufanhebung_TSommerAus",
            ),
            (
                "Heizung_Heizkreis1_OHeizgrenze",
                13739,
                "Heizung_Heizkreis1_TSommerTag",
            ),
            (
                "Heizung_Heizkreis1_OEnergiesparbetrieb",
                14158,
                "Heizung_Heizkreis1_Energiesparbetrieb_dtPause",
            ),
            (
                "Solar_Wf1_ExtWT_OZieltemperatur",
                4091,
                "Solar_Wf1_ExtWT_ZieltemperaturSekundaer",
            ),
            (
                "Anlage_WTUmwaelzpumpe_ONachlauf",
                10647,
                "Anlage_WTUmwaelzpumpe_Nachlaufzeit",
            ),
            (
                "Anlage_WTUmwaelzpumpe_ORuhemodus",
                10650,
                "Anlage_WTUmwaelzpumpe_RuhemodusDauer",
            ),
            (
                "Heizung_Wf2_ThBwErwaermung_ONachlaufzeit",
                11527,
                "Heizung_Wf2_ThBwErwaermung_Nachlaufzeit",
            ),
        ):
            target = entity(flag, "switch")
            number_id = entity(companion, "number")
            assert target and number_id
            original = hass.states.get(number_id).state
            before = Peer.saves
            await hass.services.async_call(
                "switch", "turn_off", {"entity_id": target}, blocking=True
            )
            await hass.async_block_till_done()
            assert Peer.settings[index] == 0 and Peer.saves == before + 1
            assert hass.states.get(target).state == "off"
            assert hass.states.get(number_id).state == "unavailable"
            await hass.services.async_call(
                "switch", "turn_on", {"entity_id": target}, blocking=True
            )
            await hass.async_block_till_done()
            assert Peer.settings[index] == 1 and Peer.saves == before + 2
            assert hass.states.get(target).state == "on"
            assert hass.states.get(number_id).state == original

        matrix_checkpoint("persistent flags PASS")
        # Never remove the last proven function-block predicate.
        first = entity("Anlage_Wf5_Funktionsblock_OThermostat1", "switch")
        second = entity("Anlage_Wf5_Funktionsblock_OThermostat2", "switch")
        differential = entity("Anlage_Wf5_Funktionsblock_ODifferenz", "switch")
        before_thermal = Peer.saves
        for option in (second, differential):
            await hass.services.async_call(
                "switch", "turn_off", {"entity_id": option}, blocking=True
            )
        try:
            await hass.services.async_call(
                "switch", "turn_off", {"entity_id": first}, blocking=True
            )
        except HomeAssistantError:
            pass
        else:
            raise AssertionError("Last function-block predicate removed")
        assert Peer.saves == before_thermal + 2
        for option in (second, differential):
            await hass.services.async_call(
                "switch", "turn_on", {"entity_id": option}, blocking=True
            )
        assert Peer.saves == before_thermal + 4

        seasonal = entity("Heizung_Heizkreis1_Heizgrenze", "select")
        night = entity("Heizung_Heizkreis1_TSommerNacht", "number")
        before_seasonal = Peer.saves
        await set_mode(seasonal, "Day")
        assert Peer.settings[13740] == 0
        assert hass.states.get(night).state == "unavailable"
        await set_mode(seasonal, "Day/Night")
        assert Peer.settings[13740] == 1
        assert hass.states.get(night).state != "unavailable"
        await set_mode(seasonal, "Day/Night")
        assert Peer.saves == before_seasonal + 2

        heating_mode = entity("Heizung_Heizkreis1_Betriebsart", "select")
        assert heating_mode and hass.states.get(heating_mode).state == "Auto"
        assert not entity("Heizung_Heizkreis2_Betriebsart", "select")

        async def reject_mode(target, option):
            try:
                await set_mode(target, option)
            except HomeAssistantError:
                pass
            else:
                raise AssertionError("Invalid heating mode accepted")

        before = Peer.saves
        await reject_mode(heating_mode, "Holiday")
        assert Peer.saves == before
        denied = await dashboard_request(
            readonly,
            "deltasol/dashboard/action",
            entry_id=entry.entry_id,
            entity_id=heating_mode,
            value="Day",
        )
        assert not denied["success"] and Peer.saves == before
        changed = await dashboard_request(
            resident,
            "deltasol/dashboard/action",
            entry_id=entry.entry_id,
            entity_id=heating_mode,
            value="Day",
        )
        assert changed["success"] and changed["result"] == {
            "outcome": "observed",
            "value": "Day",
        }
        await set_mode(heating_mode, "Day")
        assert Peer.saves == before + 1 and Peer.settings[14014] == 1
        await number("Urlaubsfunktion_Tage", 7)
        await set_mode(heating_mode, "Holiday")
        assert Peer.settings[14014] == 4
        holiday = entity("Heizung_Heizkreis1_OUrlaub", "switch")
        assert holiday
        await hass.services.async_call(
            "switch", "turn_on", {"entity_id": holiday}, blocking=True
        )
        assert Peer.settings[14176] == 1
        assert Peer.settings[14014] == 4  # not an implicit operating-mode write
        await reject_mode(heating_mode, "Auto")
        assert Peer.saves == before + 4
        await number("Urlaubsfunktion_Tage", 0)
        await set_mode(heating_mode, "Day")
        assert Peer.saves == before + 6 and Peer.settings[14014] == 1
        Peer.extra_values[14534] = 0
        await entry.runtime_data.relay_coordinator.async_refresh()
        await hass.async_block_till_done()
        independent = entity("Heizung_Heizkreis2_Betriebsart", "select")
        assert independent
        await set_mode(independent, "Night")
        Peer.extra_values[14534] = 1
        await reject_mode(independent, "Off")
        assert Peer.saves == before + 7 and Peer.settings[14509] == 2
        assert hass.states.get(independent).state == "unavailable"
        assert entity("Heizung_Heizkreis1_Betriebsart", "select") == heating_mode

        matrix_checkpoint("heating modes PASS")
        # Operational flags are persisted through the existing writer. A
        # disabled parent hides its child without rewriting that preference.
        for name, index in (
            ("Heizung_Wf2_OUrlaubAus", 11432),
            ("Heizung_Wf2_OSolarAus", 11433),
            ("Heizung_Wf2_SolarAus_OTSoll", 11435),
            ("Heizung_Heizkreis1_Nachheizung_OSolarAus", 14141),
            ("Heizung_Heizkreis1_Nachheizung_SolarAus_OTSoll", 14143),
            ("Solar_Wf8_NhUnterdrueckung_OSpSoll", 5302),
        ):
            switch = entity(name, "switch")
            assert switch and hass.states.get(switch).state == "off", name
            before_coordination = Peer.saves
            await hass.services.async_call(
                "switch", "turn_on", {"entity_id": switch}, blocking=True
            )
            await hass.async_block_till_done()
            assert Peer.settings[index] == 1
            assert Peer.saves == before_coordination + 1

        arrangement_activity = entity("Anlage_Wf1_Aktiviert_Modus", "select")
        assert arrangement_activity
        target = entity("Anlage_Wf1_Mischer_TZiel", "number")
        assert target
        before_activity = Peer.saves
        previous_target = Peer.settings[6629]
        await set_mode(arrangement_activity, "Deactivated")
        assert Peer.settings[6566] == 0
        assert hass.states.get(target).state == "unavailable"
        await set_mode(arrangement_activity, "Deactivated")
        assert Peer.saves == before_activity + 1
        await set_mode(arrangement_activity, "External switch")
        assert Peer.settings[6566] == 2
        await set_mode(arrangement_activity, "Activated")
        assert Peer.settings[6566] == 1
        assert Peer.settings[6629] == previous_target
        assert hass.states.get(target).state != "unavailable"
        assert Peer.saves == before_activity + 3
        circulation_activity = entity("Heizung_Wf1_Aktiviert_Modus", "select")
        assert circulation_activity
        # Change only the independent peer's installed variant. The native HA
        # action edits activity, not allocation, sensor or variant preferences.
        for variant in range(5):
            Peer.extra_values[11405] = variant
            await entry.runtime_data.relay_coordinator.async_refresh()
            await hass.async_block_till_done()
            await set_mode(circulation_activity, "Deactivated")
            before = Peer.saves
            if variant in (1, 2, 4):
                Peer.extra_values[18427] = 0
                await reject_mode(circulation_activity, "Activated")
                assert Peer.saves == before
                Peer.extra_values[18427] = 1
            if variant in (3, 4):
                Peer.extra_values[11311] = 0
                await reject_mode(circulation_activity, "External switch")
                assert Peer.saves == before
                Peer.extra_values[11311] = 3
            await set_mode(circulation_activity, "External switch")
            await set_mode(circulation_activity, "Activated")
            await set_mode(circulation_activity, "Activated")
            assert Peer.saves == before + 2
            assert Peer.settings[11273] == 1
            assert Peer.extra_values[11405] == variant
            assert Peer.extra_values[11346] == 2
        Peer.extra_values[11405] = 0
        await entry.runtime_data.relay_coordinator.async_refresh()
        await hass.async_block_till_done()
        # Existing dedicated solar functions use the same activity owner.
        for root, index, companion in (
            ("Solar_Wf1_Roehrenkollektor", 3834, "_Laufzeit"),
            ("Solar_Wf1_Bypass", 3888, "_dTein"),
            ("Solar_Wf1_CsBypass", 3981, "_Einstrahlung"),
            ("Solar_Wf1_Zieltemperatur", 4335, "_Zieltemperatur"),
        ):
            activity = entity(root + "_Aktiviert_Modus", "select")
            target = entity(root + companion, "number")
            assert activity and target
            before = Peer.saves
            previous_value = hass.states.get(target).state
            await set_mode(activity, "Deactivated")
            assert Peer.settings[index] == 0
            assert hass.states.get(target).state == "unavailable"
            await set_mode(activity, "External switch")
            assert Peer.settings[index] == 2
            await set_mode(activity, "Activated")
            await set_mode(activity, "Activated")
            assert Peer.saves == before + 3
            assert hass.states.get(target).state == previous_value

        matrix_checkpoint("activity variants PASS")
        delay_root = "Anlage_Wf5_Funktionsblock"
        delay_flag = entity(delay_root + "_OMindestausschaltzeit", "switch")
        delay = entity(delay_root + "_dtMindestausschaltzeit", "number")
        assert delay_flag and delay
        before = Peer.saves
        original_duration = Peer.settings[7748]
        for service, raw in (("turn_off", 0), ("turn_on", 1), ("turn_on", 1)):
            await hass.services.async_call(
                "switch", service, {"entity_id": delay_flag}, blocking=True
            )
            await hass.async_block_till_done()
            assert Peer.settings[7747] == raw
            assert (hass.states.get(delay).state == "unavailable") == (raw == 0)
            assert Peer.settings[7748] == original_duration
        assert Peer.saves == before + 2

        for root, suffix, index in (
            ("Anlage_Wf5_Funktionsblock", "OBezugsrelais", 7739),
            ("Anlage_Wf9_Parallelrelais", "ONachlauf", 8629),
            ("Anlage_Wf9_Parallelrelais", "OVerzoegerung", 8631),
        ):
            flag = entity(root + "_" + suffix, "switch")
            assert flag
            before = Peer.saves
            await hass.services.async_call(
                "switch", "turn_on", {"entity_id": flag}, blocking=True
            )
            await hass.async_block_till_done()
            assert Peer.settings[index] == 1 and Peer.saves == before + 1
            await hass.services.async_call(
                "switch", "turn_on", {"entity_id": flag}, blocking=True
            )
            assert Peer.saves == before + 1
        logic = entity("Anlage_Wf5_Funktionsblock_Bezugsrelais_Modus", "select")
        assert logic
        before = Peer.saves
        await set_mode(logic, "NOR")
        assert Peer.settings[7740] == 2
        Peer.extra_values[7741] = 259  # self-reference to actor 4's relay 3
        await reject_mode(logic, "AND")
        assert Peer.settings[7740] == 2 and Peer.saves == before + 1
        Peer.extra_values[7741] = 257
        await entry.runtime_data.relay_coordinator.async_refresh()
        await hass.async_block_till_done()
        await set_mode(logic, "OR")
        assert Peer.settings[7740] == 0
        assert Peer.extra_values[7596] == 4 and Peer.extra_values[7741] == 257

        parallel_activity = entity("Anlage_Wf9_Aktiviert_Modus", "select")
        assert parallel_activity
        before = Peer.saves
        await set_mode(parallel_activity, "Deactivated")
        assert Peer.settings[8574] == 0
        await set_mode(parallel_activity, "External switch")
        assert Peer.settings[8574] == 2
        await set_mode(parallel_activity, "Activated")
        assert Peer.settings[8574] == 1 and Peer.saves == before + 3
        assert Peer.extra_values[8600] == 71 and Peer.extra_values[8624] == 257
        assert Peer.settings[8629] == Peer.settings[8631] == 1

        runtime = entity("Heizung_RelaisAnforderung1_OMindestlaufzeit", "switch")
        duration = entity("Heizung_RelaisAnforderung1_Mindestlaufzeit_tMin", "number")
        assert runtime and duration
        before = Peer.saves
        original_duration = Peer.settings[10799]
        for action, expected in (("turn_off", 0), ("turn_on", 1)):
            await hass.services.async_call(
                "switch", action, {"entity_id": runtime}, blocking=True
            )
            await hass.async_block_till_done()
            assert Peer.settings[10798] == expected
            assert (hass.states.get(duration).state == "unavailable") == (expected == 0)
            assert Peer.settings[10799] == original_duration
        assert Peer.saves == before + 2

        room_activation = entity(
            "Heizung_Heizkreis1_Raumthermostat1_Aktiviert", "select"
        )
        assert room_activation
        before_room = Peer.saves
        await set_mode(room_activation, "Deactivated")
        assert Peer.settings[13820] == 0
        assert Peer.settings[13830] > 0
        await set_mode(room_activation, "Activated")
        assert Peer.settings[13820] == 1
        assert Peer.saves == before_room + 2
        for native, choices, index in (
            ("Heizung_RelaisPumpe1_Anlauf_Modus", ("Temperature", "Time"), 11178),
            ("Heizung_Heizkreis1_Nachheizung_Modus", ("On/Off", "Thermostat"), 13706),
        ):
            mode_entity = entity(native, "select")
            assert mode_entity
            before = Peer.saves
            original = Peer.settings[index]
            for choice in choices:
                await set_mode(mode_entity, choice)
            assert Peer.settings[index] == original
            assert Peer.saves == before + 2
        dhw_mode = entity("Heizung_Wf2_ThBwErwaermung_Modus", "select")
        cooling_mode = entity("Heizung_Heizkreis1_Kuehlung_Modus", "select")
        assert dhw_mode and cooling_mode
        before_modes = Peer.saves
        await set_mode(dhw_mode, "Zone")
        assert Peer.settings[11529] == 1
        await set_mode(dhw_mode, "Zone")
        assert Peer.saves == before_modes + 1
        await set_mode(dhw_mode, "Thermostat")
        await set_mode(cooling_mode, "Both")
        assert Peer.settings[14066] == 2
        await set_mode(cooling_mode, "Outdoor")
        assert Peer.saves == before_modes + 4
        for name, index in (
            ("Heizung_Wf2_OBetriebsartenschalterAus", 11438),
            ("Heizung_Wf2_ThBwErwaermung_OManuelleAufheizung", 11538),
            ("Solar_Kol1_Omin", 3533),
        ):
            option = entity(name, "switch")
            assert option and hass.states.get(option).state == "off"
            before_option = Peer.saves
            await hass.services.async_call(
                "switch", "turn_on", {"entity_id": option}, blocking=True
            )
            await hass.async_block_till_done()
            assert Peer.settings[index] == 1
            assert Peer.saves == before_option + 1

        loading = entity("Solar_Beladungstyp", "select")
        store_activity = entity("Solar_Sp1_Aktiviert_Modus", "select")
        assert loading and store_activity
        before_solar = Peer.saves
        await set_mode(loading, "Successive")
        assert Peer.settings[3417] == 1
        await set_mode(loading, "Pendulum")
        assert Peer.settings[3417] == 0
        before_store = {i: Peer.settings[i] for i in (3579, 3582, 3585, 3588, 3591)}
        await set_mode(store_activity, "Deactivated")
        assert Peer.settings[3604] == 0
        assert (
            hass.states.get(entity("Solar_Sp1_dTein", "number")).state == "unavailable"
        )
        await set_mode(store_activity, "Activated")
        assert Peer.settings[3604] == 1
        assert all(Peer.settings[i] == value for i, value in before_store.items())
        assert Peer.saves == before_solar + 4

        pulse = entity("Solar_Losreissimpuls", "number")
        assert pulse
        before_pulse = Peer.saves
        await hass.services.async_call(
            "number", "set_value", {"entity_id": pulse, "value": 25}, blocking=True
        )
        await hass.async_block_till_done()
        assert Peer.settings[3438] == 25 and Peer.saves == before_pulse + 1
        assert float(hass.states.get(pulse).state) == 25

        circuit_activity = entity("Heizung_Heizkreis1_Aktiviert_Modus", "select")
        analog_manual = entity("Ausgang_Regler_A1_Handbetrieb", "select")
        assert circuit_activity and analog_manual
        before_circuit = Peer.saves
        assignments = {
            i: Peer.extra_values[i] for i in (13764, 13772, 13780, 13796, 13800)
        }
        await set_mode(circuit_activity, "Deactivated")
        assert Peer.settings[13689] == 0
        await set_mode(circuit_activity, "External switch")
        assert Peer.settings[13689] == 2
        await set_mode(circuit_activity, "Activated")
        assert Peer.settings[13689] == 1 and Peer.saves == before_circuit + 3
        assert all(Peer.extra_values[i] == value for i, value in assignments.items())
        before_manual = Peer.writes
        before_save = Peer.saves
        await reject_mode(analog_manual, "Min")
        await set_mode(analog_manual, "On")
        await set_mode(analog_manual, "Auto")
        assert Peer.extra_values[2612] == 2
        assert Peer.writes == before_manual + 2 and Peer.saves == before_save

        matrix_checkpoint("remaining native options PASS")
        timer = entity("Anlage_Wf8_OSchaltuhr", "switch")
        assert timer and hass.states.get(timer).state == "off"
        assert hass.states.get(timer).attributes["weekly_program_reference"] == 2
        before_timer = Peer.saves
        week_before = {idx: list(Peer.weekly_words[idx]) for idx in range(18432, 18439)}

        async def timer_action(action):
            await hass.services.async_call(
                "switch", action, {"entity_id": timer}, blocking=True
            )
            await hass.async_block_till_done()

        Peer.extra_values[18427] = 0
        try:
            await timer_action("turn_on")
        except HomeAssistantError:
            pass
        else:
            raise AssertionError("Expired weekly program enabled")
        assert Peer.saves == before_timer
        Peer.extra_values[18427] = 1
        await entry.runtime_data.relay_coordinator.async_refresh()
        await timer_action("turn_on")
        assert Peer.settings[8331] == 1 and Peer.saves == before_timer + 1
        day_two = entity("Wsu2_Schaltuhr_Woche_TagMo_Daten", "text")
        assert day_two and hass.states.get(day_two).state != "unavailable"
        await timer_action("turn_on")
        assert Peer.saves == before_timer + 1
        await timer_action("turn_off")
        assert Peer.settings[8331] == 0 and Peer.saves == before_timer + 2
        assert hass.states.get(day_two).state == "unavailable"
        assert Peer.extra_values[8377] == 2
        assert all(
            Peer.weekly_words[idx] == words for idx, words in week_before.items()
        )
        await timer_action("turn_on")
        assert Peer.saves == before_timer + 3

        schedule = entity("Wsu1_Schaltuhr_Woche_TagMo_Daten", "text")
        assert (
            schedule and hass.states.get(schedule).state == "08:00-10:00, 18:00-20:00"
        )
        assert set(hass.states.get(schedule).attributes["observed_references"]) == {
            "Anlage_Wf1_SchaltuhrNr",
            "Anlage_Wf4_SchaltuhrNr",
        }
        assert hass.states.get(schedule).attributes["active_references"] == [
            "Anlage_Wf4_SchaltuhrNr"
        ]
        request = {
            "entry_id": entry.entry_id,
            "entity_id": schedule,
            "value": "08:30-10:00, 18:00-20:00",
        }
        preview = await dashboard_request(
            admin, "deltasol/dashboard/action", preview=True, **request
        )
        assert preview["success"] and Peer.bulk_saves == 0
        review = preview["result"]
        assert review["expected"] == "08:00-10:00, 18:00-20:00"
        assert len(review["references"]) == 2
        denial = await dashboard_request(
            resident, "deltasol/dashboard/action", preview=True, **request
        )
        assert not denial["success"] and Peer.bulk_saves == 0
        # The reviewed HA action itself must reject an unauthorized caller,
        # independently of dashboard visibility and websocket checks.
        try:
            await hass.services.async_call(
                "deltasol",
                "save_reviewed_weekly_program",
                {
                    **request,
                    "expected": review["expected"],
                    "reference_token": review["reference_token"],
                },
                blocking=True,
                context=Context(user_id=resident.id),
            )
        except HomeAssistantError:
            pass
        else:
            raise AssertionError("Resident invoked reviewed weekly service")
        assert Peer.bulk_saves == 0
        stale = await dashboard_request(
            admin,
            "deltasol/dashboard/action",
            **request,
            expected="",
            references=review["references"],
            reference_token=review["reference_token"],
        )
        assert not stale["success"] and Peer.bulk_saves == 0
        # Two independently authenticated dashboard clients review the same
        # old day. Exactly one may commit, even when both request the same value.
        competing = await asyncio.gather(
            *(
                dashboard_request(
                    admin,
                    "deltasol/dashboard/action",
                    **request,
                    expected=review["expected"],
                    references=review["references"],
                    reference_token=review["reference_token"],
                )
                for _ in range(2)
            )
        )
        successful = [result for result in competing if result["success"]]
        assert len(successful) == 1, competing
        assert successful[0]["result"]["outcome"] == "observed"
        assert hass.states.get(schedule).state == "08:30-10:00, 18:00-20:00"
        assert Peer.weekly_words[18419] == [0x50482822, -1, -1]
        assert Peer.bulk_saves == 1
        await hass.services.async_call(
            "text",
            "set_value",
            {"entity_id": schedule, "value": "08:30-10:00, 18:00-20:00"},
            blocking=True,
        )
        assert Peer.bulk_saves == 1

        async def rejected_schedule(value):
            try:
                await hass.services.async_call(
                    "text",
                    "set_value",
                    {"entity_id": schedule, "value": value},
                    blocking=True,
                )
            except HomeAssistantError as error:
                return str(error)
            raise AssertionError("Invalid or uncertain weekly action accepted")

        await rejected_schedule("08:01-10:00")
        assert Peer.bulk_saves == 1
        # The allocation expires after the last displayed snapshot.
        Peer.extra_values[18414] = 0
        await rejected_schedule("09:00-10:00")
        assert Peer.bulk_saves == 1
        assert hass.states.get(schedule).state == "unavailable"
        Peer.extra_values[18414] = 1
        await entry.runtime_data.relay_coordinator.async_refresh()
        assert entity("Wsu1_Schaltuhr_Woche_TagMo_Daten", "text") == schedule
        assert hass.states.get(schedule).state == "08:30-10:00, 18:00-20:00"

        matrix_checkpoint("weekly program PASS")
        # Restore production pacing before the uncertain packed write and
        # active capture shutdown/restart lifecycle cases.
        semantic_pacing.close()
        print("ISOLATED_HA native semantic matrix PASS", flush=True)

        # Lose the first stage acknowledgement after it changed the day.
        # The requested third interval lives in word two, which is never sent.
        Peer.fail_bulk_subindex = 0
        error = await rejected_schedule("06:00-07:00, 09:00-10:00, 11:00-12:00")
        assert "outcome uncertain: partial" in error
        assert Peer.bulk_saves == 1
        assert Peer.weekly_words[18419] == [0x28241C18, -1, -1]
        assert hass.states.get(schedule).state == "06:00-07:00, 09:00-10:00"
        # Explicit subsequent operator action, never an automatic retry.
        await hass.services.async_call(
            "text",
            "set_value",
            {"entity_id": schedule, "value": "08:30-10:00, 18:00-20:00"},
            blocking=True,
        )
        assert Peer.bulk_saves == 2
        assert json.loads(Peer.weekly_file.read_text(encoding="utf-8"))["18419"] == [
            0x50482822,
            -1,
            -1,
        ]

        # Stop the actual HA process with an incomplete active capture. The
        # following restart phase must restore the same committed checkpoint.
        with (
            patch("custom_components.deltasol.discovery.BATCH_SIZE", 1),
            patch("custom_components.deltasol.DISCOVERY_BATCH_GAP_SECONDS", 3),
        ):
            await hass.services.async_call(
                "button", "press", {"entity_id": button}, blocking=True
            )
            capture = entry.runtime_data.capture
            async with asyncio.timeout(20):
                while capture.offset < 1:
                    await asyncio.sleep(0.05)
            assert capture.status == "running" and capture.offset == 1
            marker = {"started_at": capture.started_at, "offset": capture.offset}
            (directory / "qualification-active.json").write_text(
                json.dumps(marker), encoding="utf-8"
            )
            await hass.async_stop(force=True)
            stopped = True
        checkpoint = json.loads(
            (directory / ".storage" / f"deltasol.{entry.entry_id}.discovery").read_text(
                encoding="utf-8"
            )
        )["data"]
        assert checkpoint["resume"] is True
        assert checkpoint["capture"]["started_at"] == marker["started_at"]
        assert 1 <= checkpoint["capture"]["offset"] < expected_config_count
    finally:
        semantic_pacing.close()
        try:
            if not stopped:
                await hass.async_stop(force=True)
        finally:
            candidate_filter.__exit__(None, None, None)


def main():
    directory = Path(sys.argv[1])
    phase = sys.argv[2]
    additional_relays()
    additional_configuration()
    Peer.persistent_file = directory / "qualification-peer-settings.json"
    Peer.weekly_file = directory / "qualification-peer-weekly.json"
    if Peer.weekly_file.exists():
        Peer.weekly_words = {
            int(index): words
            for index, words in json.loads(
                Peer.weekly_file.read_text(encoding="utf-8")
            ).items()
        }
    if Peer.persistent_file.exists():
        Peer.settings = {
            int(index): value
            for index, value in json.loads(
                Peer.persistent_file.read_text(encoding="utf-8")
            ).items()
        }
    with (
        ThreadingHTTPServer(("127.0.0.1", 18080), HttpHandler) as http,
        ThreadingHTTPServer(("127.0.0.1", 18081), HttpHandler) as secondary,
        VBusServer(("127.0.0.1", 7053), VBusHandler) as vbus,
    ):
        for server in (http, secondary, vbus):
            threading.Thread(target=server.serve_forever, daemon=True).start()
        asyncio.run(qualify(directory, phase))
    print(f"ISOLATED_HA {phase} PASS (controlled loopback protocol peer)")


if __name__ == "__main__":
    main()

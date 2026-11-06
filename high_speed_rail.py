"""High-speed rail: the links between the cities on the lines, by gravity score.

Each link joins two cities next to each other on a high-speed line and is
scored as a gravity model scores it, after a #30DayMapChallenge map of Rail
Baltica's segments by "gravity score"::

    gravity = (P1 × P2 / 100,000) / (2 × t)

with P1 and P2 the people in each city and t the fastest scheduled journey
between them in minutes. The links are drawn wider and darker the higher
they score, each labelled with its score where it fits.

Data
----
The lines are those built for 250 km/h or more, and the lines upgraded for
about 200 km/h that join them, with high-speed trains on them in 2026: in
Europe, East Asia (China, Japan, South Korea and Taiwan) and, fewer, Turkey,
Russia, Morocco, Saudi Arabia, Uzbekistan, Indonesia and the United States'
Northeast Corridor. ``planned`` adds lines under construction or planned, with
their promised journey times: Rail Baltica, HS2, the Chūō Shinkansen,
Budapest-Belgrade, Lisbon-Porto, Brescia-Verona-Padua (today's times on the old
line, as a guide) and Mumbai-Ahmedabad.

The cities, the stations' places and the journey times (the fastest train
between two neighbouring cities, from the 2025-26 timetables, in round
minutes) were compiled for this module and are approximate; the intermediate
stations of towns too small to be an urban centre are left out. The links in
view are drawn along their track, from OpenStreetMap's high-speed tracks (see
:mod:`rail_tracks`: downloaded once per region, ODbL); a link OSM has no track
for, or with the tracks not to be had, is drawn from city to city through a
few points along its way (:data:`VIA`). A city's population is that of the GHSL urban centre its station
is in (GHS-UCDB R2024A, 2025: European Commission, JRC, CC BY 4.0), the
metropolitan area as it is built, so the Pearl River Delta is one city
(Guangzhou with Shenzhen) and Tokyo takes in Yokohama: a link within one urban
centre is drawn but not scored. The few towns too small or sparse to be an
urban centre (Toledo, Cuenca, Segovia, Zamora, Santiago de Compostela) have
their people in GeoNames (cities5000, CC BY 4.0).
"""

from __future__ import annotations

import logging
import math
from typing import Any, NamedTuple

import numpy as np

import cartopy.crs as ccrs
from cartopy.mpl.geoaxes import GeoAxes
from matplotlib.collections import LineCollection
from matplotlib.legend import Legend
from matplotlib.lines import Line2D

import frame
from air_routes import great_circle, near_side
from rail_tracks import TRACKS_ATTRIBUTION, TrackRouter
from biomes import on_globe
from cities import label_space

logger = logging.getLogger(__name__)

CHOICES = {"open": "the lines open in 2026", "planned": "and those planned or under construction, dashed"}
DIVISOR = 100_000
# The classes of gravity score, a power of ten apart, each a width and a colour (ColorBrewer RdPu)
BREAKS = (1_000, 10_000, 100_000, 1_000_000, 10_000_000)
COLOURS = ("#fa9fb5", "#f768a1", "#dd3497", "#ae017e", "#7a0177", "#49006a")
WIDTHS = (0.9, 1.5, 2.3, 3.3, 4.6, 6.2)
WITHIN_COLOUR = "#969696"         # a link within one urban centre, which a gravity score cannot weigh
STATION_COLOUR = "#f16913"
LABEL_MAX = 60
SCORE_LABEL_MAX = 80

HIGH_SPEED_RAIL_ATTRIBUTION = ("High-speed rail: lines and fastest scheduled times compiled for ortho (2025-26, "
                               "approximate); populations GHS-UCDB R2024A 2025 (European Commission, JRC) and "
                               "GeoNames, CC BY 4.0")

# The cities: where their high-speed station is (lat, lon), the people in the GHSL urban centre it is in in 2025,
# and that centre's name in GHSL; a town too small or sparse to be an urban centre ("") has its people in GeoNames
STATIONS: dict[str, tuple[float, float, int, str]] = {
    # France
    "Paris": (48.8443, 2.3744, 9328385, "Paris"),
    "Lyon": (45.7606, 4.8594, 1194741, "Lyon"),
    "Avignon": (43.9216, 4.7861, 78051, "Avignon"),
    "Marseille": (43.3027, 5.3806, 911934, "Marseille"),
    "Nîmes": (43.8325, 4.3660, 131979, "Nîmes"),
    "Montpellier": (43.6047, 3.8807, 337381, "Montpellier"),
    "Lille": (50.6393, 3.0757, 907123, "Lille"),
    "Reims": (49.2590, 4.0240, 190718, "Reims"),
    "Strasbourg": (48.5850, 7.7350, 419245, "Strasbourg"),
    "Dijon": (47.3233, 5.0272, 219337, "Dijon"),
    "Besançon": (47.2470, 6.0230, 98834, "Besançon"),
    "Mulhouse": (47.7425, 7.3427, 187082, "Mulhouse"),
    "Tours": (47.3858, 0.7240, 223834, "Tours"),
    "Poitiers": (46.5820, 0.3335, 93032, "Poitiers"),
    "Angoulême": (45.6539, 0.1652, 54289, "Angoulême"),
    "Bordeaux": (44.8256, -0.5562, 642756, "Bordeaux"),
    "Le Mans": (47.9952, 0.1920, 140414, "Le Mans"),
    "Rennes": (48.1035, -1.6723, 251924, "Rennes"),
    "Perpignan": (42.6960, 2.8796, 124596, "Perpignan"),
    # Britain, Belgium and the Netherlands
    "London": (51.5317, -0.1263, 10408332, "London"),
    "Birmingham": (52.4797, -1.8850, 2617211, "Birmingham"),
    "Brussels": (50.8357, 4.3360, 1468754, "Brussels"),
    "Antwerp": (51.2172, 4.4211, 767652, "Antwerp"),
    "Liège": (50.6245, 5.5667, 373662, "Liège"),
    "Rotterdam": (51.9244, 4.4690, 2173754, "Rotterdam [The Hague]"),
    "Amsterdam": (52.3791, 4.9003, 1223407, "Amsterdam"),
    # Germany
    "Aachen": (50.7678, 6.0912, 218507, "Aachen"),
    "Cologne": (50.9430, 6.9589, 1156303, "Cologne"),
    "Frankfurt": (50.1071, 8.6638, 916259, "Frankfurt"),
    "Mannheim": (49.4794, 8.4695, 456549, "Mannheim"),
    "Stuttgart": (48.7840, 9.1817, 909232, "Stuttgart"),
    "Ulm": (48.3994, 9.9829, 115346, "Ulm"),
    "Augsburg": (48.3654, 10.8855, 338257, "Augsburg"),
    "Munich": (48.1402, 11.5600, 1827704, "Munich"),
    "Nuremberg": (49.4456, 11.0828, 645853, "Nuremberg"),
    "Erfurt": (50.9725, 11.0380, 174628, "Erfurt"),
    "Leipzig": (51.3455, 12.3821, 585335, "Leipzig"),
    "Berlin": (52.5251, 13.3694, 3549997, "Berlin"),
    "Hamburg": (53.5530, 10.0067, 1733898, "Hamburg"),
    "Hanover": (52.3767, 9.7410, 663997, "Hanover"),
    "Wolfsburg": (52.4293, 10.7880, 70340, "Wolfsburg"),
    "Göttingen": (51.5365, 9.9269, 110420, "Göttingen"),
    "Kassel": (51.3132, 9.4469, 238931, "Kassel"),
    "Fulda": (50.5546, 9.6839, 53458, "Fulda"),
    "Würzburg": (49.8017, 9.9358, 120858, "Würzburg"),
    "Karlsruhe": (48.9935, 8.4020, 299990, "Karlsruhe"),
    "Freiburg": (47.9977, 7.8411, 238078, "Freiburg im Breisgau"),
    # Switzerland, Austria and Italy
    "Basel": (47.5476, 7.5897, 285823, "Basel"),
    "Zurich": (47.3779, 8.5403, 689208, "Zurich"),
    "Bern": (46.9490, 7.4391, 193474, "Bern"),
    "Lugano": (46.0054, 8.9469, 91995, "Lugano"),
    "Vienna": (48.1855, 16.3780, 2056565, "Vienna"),
    "Linz": (48.2904, 14.2914, 243243, "Linz"),
    "Salzburg": (47.8130, 13.0458, 143452, "Salzburg"),
    "Graz": (47.0725, 15.4163, 306742, "Graz"),
    "Klagenfurt": (46.6160, 14.3125, 80261, "Klagenfurt"),
    "Turin": (45.0718, 7.6655, 1243448, "Turin"),
    "Milan": (45.4864, 9.2045, 3135553, "Milan"),
    "Brescia": (45.5329, 10.2127, 243287, "Brescia"),
    "Verona": (45.4292, 10.9822, 216582, "Verona"),
    "Padua": (45.4176, 11.8800, 244498, "Padua"),
    "Venice": (45.4410, 12.3210, 61951, "Venice"),
    "Bologna": (44.5058, 11.3431, 456780, "Bologna"),
    "Florence": (43.7764, 11.2480, 711105, "Florence"),
    "Rome": (41.9010, 12.5016, 2476089, "Rome"),
    "Naples": (40.8530, 14.2730, 2885079, "Naples"),
    "Salerno": (40.6748, 14.7724, 128967, "Salerno"),
    # Spain and Portugal
    "Madrid": (40.4066, -3.6900, 5680137, "Madrid"),
    "Toledo": (39.8620, -4.0160, 86526, ""),
    "Ciudad Real": (38.9845, -3.9180, 54362, "Ciudad Real"),
    "Córdoba": (37.8885, -4.7895, 239916, "Córdoba"),
    "Seville": (37.3920, -5.9752, 696193, "Seville"),
    "Málaga": (36.7118, -4.4320, 522827, "Málaga"),
    "Zaragoza": (41.6582, -0.9113, 624271, "Zaragoza"),
    "Lleida": (41.6205, 0.6332, 138521, "Lleida"),
    "Tarragona": (41.1917, 1.2712, 152944, "Tarragona"),
    "Barcelona": (41.3794, 2.1400, 3908362, "Barcelona"),
    "Girona": (41.9790, 2.8170, 122502, "Girona"),
    "Cuenca": (40.0445, -2.1453, 54898, ""),
    "Valencia": (39.4594, -0.3829, 1404208, "Valencia"),
    "Albacete": (38.9990, -1.8495, 137538, "Albacete"),
    "Alicante": (38.3440, -0.4950, 400711, "Alicante"),
    "Murcia": (37.9745, -1.1295, 341431, "Murcia"),
    "Segovia": (40.9122, -4.0973, 51683, ""),
    "Valladolid": (41.6440, -4.7290, 309896, "Valladolid"),
    "León": (42.5935, -5.5770, 145844, "León"),
    "Oviedo": (43.3653, -5.8569, 219999, "Oviedo / Uviéu"),
    "Burgos": (42.3710, -3.6640, 151942, "Burgos"),
    "Zamora": (41.5147, -5.7364, 61827, ""),
    "Ourense": (42.3500, -7.8740, 90571, "Ourense"),
    "Santiago de Compostela": (42.8710, -8.5440, 99536, ""),
    "A Coruña": (43.3540, -8.4080, 271212, "A Coruña"),
    "Vigo": (42.2340, -8.7130, 291792, "Vigo"),
    "Lisbon": (38.7678, -9.0990, 1974574, "Lisbon"),
    "Porto": (41.1490, -8.5856, 1060250, "Porto"),
    # Central and eastern Europe
    "Warsaw": (52.2287, 21.0039, 1999986, "Warsaw"),
    "Kraków": (50.0676, 19.9475, 723413, "Kraków"),
    "Katowice": (50.2575, 19.0170, 879406, "Katowice"),
    "Gdańsk": (54.3557, 18.6439, 398019, "Gdańsk"),
    "Białystok": (53.1350, 23.1390, 299386, "Białystok"),
    "Kaunas": (54.8865, 23.9104, 236910, "Kaunas"),
    "Vilnius": (54.6700, 25.2840, 339420, "Vilnius"),
    "Riga": (56.9470, 24.1200, 516897, "Riga"),
    "Tallinn": (59.4330, 24.7360, 449861, "Tallinn"),
    "Budapest": (47.5003, 19.0838, 1743875, "Budapest"),
    "Subotica": (46.0990, 19.6750, 79457, "Subotica"),
    "Novi Sad": (45.2650, 19.8300, 286753, "Novi Sad"),
    "Belgrade": (44.7952, 20.4569, 1200547, "Belgrade"),
    "Moscow": (55.7765, 37.6550, 14384082, "Moscow"),
    "Tver": (56.8370, 35.8920, 339394, "Tver"),
    "Saint Petersburg": (59.9300, 30.3620, 4878966, "Saint Petersburg"),
    "Istanbul": (40.9925, 29.0370, 14210222, "Istanbul"),
    "Eskişehir": (39.7750, 30.5140, 608474, "Eskişehir"),
    "Ankara": (39.9365, 32.8440, 3695989, "Ankara"),
    "Konya": (37.8720, 32.4910, 1017329, "Konya"),
    "Sivas": (39.7400, 37.0150, 401054, "Sivas"),
    # Africa, the Middle East and India
    "Tangier": (35.7680, -5.7900, 1300223, "Tangier"),
    "Kenitra": (34.2560, -6.5850, 588772, "Kenitra"),
    "Rabat": (34.0160, -6.8340, 2094221, "Rabat"),
    "Casablanca": (33.5890, -7.5910, 4577677, "Casablanca"),
    "Mecca": (21.3880, 39.8040, 1876451, "Mecca"),
    "Jeddah": (21.5370, 39.2300, 4842257, "Jeddah"),
    "Medina": (24.5540, 39.6880, 1565146, "Medina"),
    "Tashkent": (41.2920, 69.2860, 3874653, "Tashkent"),
    "Samarkand": (39.6760, 66.9230, 892554, "Samarqand"),
    "Bukhara": (39.7700, 64.4200, 243753, "Bukhara"),
    "Mumbai": (19.0670, 72.8670, 20453270, "Mumbai"),
    "Ahmedabad": (23.0225, 72.5970, 7898650, "Ahmedabad"),
    # China
    "Beijing": (39.8652, 116.3786, 18150576, "Beijing"),
    "Tianjin": (39.1366, 117.2050, 7330648, "Tianjin"),
    "Jinan": (36.6656, 116.8916, 3824875, "Jinan"),
    "Xuzhou": (34.2240, 117.2620, 1310475, "Xuzhou"),
    "Nanjing": (31.9690, 118.7980, 8061029, "Nanjing"),
    "Changzhou": (31.7810, 119.9690, 2772878, "Changzhou"),
    "Wuxi": (31.5880, 120.3030, 11540430, "Suzhou"),
    "Suzhou": (31.3320, 120.6110, 11540430, "Suzhou"),
    "Shanghai": (31.1945, 121.3210, 30678616, "Shanghai"),
    "Hangzhou": (30.2908, 120.2130, 6387064, "Hangzhou"),
    "Ningbo": (29.8640, 121.5380, 2953172, "Ningbo"),
    "Wenzhou": (27.9165, 120.6145, 3838182, "Wenzhou"),
    "Fuzhou": (26.1135, 119.3180, 2880410, "Fuzhou"),
    "Xiamen": (24.4800, 118.1100, 1676987, "Xiamen City"),
    "Shantou": (23.5400, 116.5800, 10579303, "Shantou"),
    "Shenzhen": (22.6090, 114.0290, 42987704, "Guangzhou"),
    "Guangzhou": (22.9890, 113.2690, 42987704, "Guangzhou"),
    "Hong Kong": (22.3040, 114.1660, 4807599, "Hong Kong"),
    "Shijiazhuang": (38.0090, 114.4800, 3100270, "Shijiazhuang"),
    "Zhengzhou": (34.7580, 113.7780, 5126112, "Zhengzhou"),
    "Wuhan": (30.6090, 114.4240, 8079484, "Wuhan"),
    "Changsha": (28.1470, 113.0640, 3246971, "Changsha"),
    "Luoyang": (34.5940, 112.4630, 1603585, "Luoyang"),
    "Xi'an": (34.3770, 108.9390, 5298992, "Xi'an"),
    "Lanzhou": (36.0690, 103.7370, 2689402, "Lanzhou"),
    "Xining": (36.6280, 101.7650, 1423665, "Xining"),
    "Chengdu": (30.6300, 104.1450, 5609627, "Chengdu"),
    "Chongqing": (29.6090, 106.5500, 8449690, "Chongqing"),
    "Guiyang": (26.6230, 106.6730, 2757868, "Guiyang"),
    "Kunming": (25.0400, 102.7100, 3218010, "Kunming"),
    "Nanchang": (28.6233, 115.7930, 3641447, "Nanchang"),
    "Hefei": (31.7990, 117.2940, 5646049, "Hefei"),
    "Shenyang": (41.8190, 123.4350, 6175250, "Shenyang"),
    "Changchun": (43.9070, 125.3230, 3519692, "Changchun"),
    "Harbin": (45.7130, 126.5900, 4591886, "Harbin"),
    "Dalian": (38.9600, 121.6000, 3599519, "Dalian"),
    "Qingdao": (36.1700, 120.3720, 3682893, "Qingdao"),
    "Taiyuan": (37.7980, 112.6150, 4332180, "Taiyuan"),
    "Zhangjiakou": (40.8400, 114.8800, 587854, "Zhangjiakou"),
    "Nanning": (22.8200, 108.3700, 3695575, "Nanning"),
    "Haikou": (19.9840, 110.3460, 1374152, "Xiuying District"),
    "Sanya": (18.3000, 109.4920, 421522, "Sanya"),
    # Japan
    "Tokyo": (35.6812, 139.7671, 33155907, "Tokyo"),
    "Shizuoka": (34.9716, 138.3890, 233904, "Shizuoka"),
    "Hamamatsu": (34.7039, 137.7350, 267033, "Hamamatsu"),
    "Nagoya": (35.1709, 136.8815, 7721742, "Nagoya"),
    "Kyoto": (34.9858, 135.7588, 12653994, "Osaka"),
    "Osaka": (34.7335, 135.5002, 12653994, "Osaka"),
    "Okayama": (34.6664, 133.9178, 847111, "Okayama"),
    "Hiroshima": (34.3976, 132.4754, 1150148, "Hiroshima"),
    "Kitakyushu": (33.8869, 130.8826, 1208935, "Kitakyushu"),
    "Fukuoka": (33.5902, 130.4207, 2213305, "Fukuoka"),
    "Kumamoto": (32.7898, 130.6886, 624648, "Kumamoto"),
    "Kagoshima": (31.5839, 130.5418, 453833, "Kagoshima"),
    "Utsunomiya": (36.5590, 139.8985, 337161, "Utsunomiya"),
    "Kōriyama": (37.3983, 140.3880, 215513, "Koriyama"),
    "Sendai": (38.2601, 140.8824, 1269675, "Sendai"),
    "Morioka": (39.7016, 141.1366, 217880, "Morioka"),
    "Aomori": (40.8270, 140.6935, 179937, "Aomori"),
    "Hakodate": (41.9050, 140.6480, 263045, "Hakodate"),
    "Takasaki": (36.3225, 139.0124, 472415, "Takasaki [Maebashi]"),
    "Niigata": (37.9120, 139.0610, 437871, "Niigata"),
    "Nagano": (36.6430, 138.1886, 264480, "Nagano"),
    "Toyama": (36.7013, 137.2134, 239820, "Toyama"),
    "Kanazawa": (36.5780, 136.6480, 527097, "Kanazawa"),
    "Fukui": (36.0620, 136.2230, 195359, "Fukui"),
    # South Korea and Taiwan
    "Seoul": (37.5547, 126.9707, 22261692, "Seoul"),
    "Daejeon": (36.3324, 127.4343, 1504229, "Daejeon"),
    "Daegu": (35.8794, 128.6286, 2300085, "Daegu"),
    "Ulsan": (35.5515, 129.1385, 887568, "Ulsan"),
    "Busan": (35.1151, 129.0422, 3263408, "Busan"),
    "Gwangju": (35.1375, 126.7910, 1428492, "Gwangju"),
    "Mokpo": (34.7915, 126.3870, 228462, "Mokpo-si"),
    "Gangneung": (37.7640, 128.8990, 118784, "Gangneung"),
    "Taipei": (25.0478, 121.5170, 9686521, "Taipei"),
    "Hsinchu": (24.8080, 121.0403, 713497, "Hsinchu"),
    "Taichung": (24.1123, 120.6157, 2318377, "Taichung"),
    "Chiayi": (23.4594, 120.3233, 460979, "Chiayi"),
    "Tainan": (22.9250, 120.2856, 1010792, "Tainan"),
    "Kaohsiung": (22.6871, 120.3075, 2294772, "Kaohsiung"),
    # Southeast Asia and North America
    "Jakarta": (-6.2470, 106.8900, 40545126, "Jakarta"),
    "Bandung": (-6.9570, 107.7590, 8691181, "Bandung"),
    "Boston": (42.3520, -71.0550, 1669310, "Boston"),
    "Providence": (41.8290, -71.4130, 437121, "Providence"),
    "New Haven": (41.2970, -72.9270, 147121, "New Haven"),
    "New York": (40.7506, -73.9935, 14197659, "New York City"),
    "Philadelphia": (39.9566, -75.1820, 2286396, "Philadelphia"),
    "Baltimore": (39.3073, -76.6157, 1127338, "Baltimore"),
    "Washington": (38.8973, -77.0063, 3471725, "Washington"),
}

# The lines: their cities in order, with the fastest journey between each two in minutes between them
OPEN_LINES: dict[str, list[str | int]] = {
    "LGV Sud-Est and Méditerranée": ["Paris", 116, "Lyon", 62, "Avignon", 32, "Marseille"],
    "LGV Méditerranée to Montpellier": ["Lyon", 80, "Nîmes", 25, "Montpellier"],
    "LGV Nord and HS1": ["London", 82, "Lille", 62, "Paris"],
    "LGV Nord to Brussels": ["Lille", 35, "Brussels"],
    "HSL 4 and HSL-Zuid": ["Brussels", 35, "Antwerp", 33, "Rotterdam", 41, "Amsterdam"],
    "HSL 2 and 3": ["Brussels", 40, "Liège", 22, "Aachen", 38, "Cologne"],
    "LGV Est": ["Paris", 46, "Reims", 80, "Strasbourg"],
    "LGV Rhin-Rhône": ["Paris", 95, "Dijon", 28, "Besançon", 40, "Mulhouse", 20, "Basel"],
    "LGV Atlantique and Sud Europe Atlantique": ["Paris", 58, "Tours", 35, "Poitiers", 38, "Angoulême", 35,
                                                 "Bordeaux"],
    "LGV Bretagne-Pays de la Loire": ["Paris", 55, "Le Mans", 40, "Rennes"],
    "Perpignan-Barcelona": ["Perpignan", 38, "Girona", 38, "Barcelona"],
    "Madrid-Barcelona": ["Madrid", 78, "Zaragoza", 47, "Lleida", 30, "Tarragona", 35, "Barcelona"],
    "Madrid-Seville": ["Madrid", 52, "Ciudad Real", 50, "Córdoba", 43, "Seville"],
    "Córdoba-Málaga": ["Córdoba", 52, "Málaga"],
    "Madrid-Toledo": ["Madrid", 33, "Toledo"],
    "Madrid-Levante": ["Madrid", 52, "Cuenca", 50, "Valencia"],
    "Madrid-Alicante-Murcia": ["Cuenca", 40, "Albacete", 55, "Alicante", 40, "Murcia"],
    "Madrid-Valladolid-León-Asturias": ["Madrid", 27, "Segovia", 33, "Valladolid", 42, "León", 60, "Oviedo"],
    "Valladolid-Burgos": ["Valladolid", 45, "Burgos"],
    "Madrid-Galicia": ["Segovia", 58, "Zamora", 60, "Ourense", 38, "Santiago de Compostela", 28, "A Coruña"],
    "Atlantic Axis": ["Santiago de Compostela", 55, "Vigo"],
    "Turin-Milan-Naples-Salerno": ["Turin", 50, "Milan", 62, "Bologna", 37, "Florence", 90, "Rome", 70, "Naples",
                                   37, "Salerno"],
    "Milan-Brescia": ["Milan", 36, "Brescia"],
    "Padua-Venice": ["Padua", 25, "Venice"],
    "Cologne-Frankfurt": ["Cologne", 62, "Frankfurt"],
    "Frankfurt-Stuttgart-Munich": ["Frankfurt", 38, "Mannheim", 38, "Stuttgart", 42, "Ulm", 40, "Augsburg", 30,
                                   "Munich"],
    "Munich-Berlin (VDE 8)": ["Munich", 62, "Nuremberg", 66, "Erfurt", 42, "Leipzig", 72, "Berlin"],
    "Hamburg-Berlin": ["Hamburg", 103, "Berlin"],
    "Hanover-Berlin": ["Hanover", 32, "Wolfsburg", 60, "Berlin"],
    "Hanover-Würzburg": ["Hamburg", 77, "Hanover", 33, "Göttingen", 18, "Kassel", 30, "Fulda", 33, "Würzburg"],
    "Fulda-Frankfurt": ["Fulda", 55, "Frankfurt"],
    "Rhine Valley Railway": ["Mannheim", 25, "Karlsruhe", 60, "Freiburg", 40, "Basel"],
    "Bern-Zurich and Gotthard": ["Bern", 56, "Zurich", 108, "Lugano", 70, "Milan"],
    "Western Railway (Austria)": ["Vienna", 70, "Linz", 68, "Salzburg"],
    "Koralm Railway": ["Graz", 45, "Klagenfurt"],
    "Central Trunk Line (CMK)": ["Warsaw", 140, "Kraków"],
    "CMK to Katowice": ["Warsaw", 145, "Katowice"],
    "Warsaw-Gdańsk": ["Warsaw", 160, "Gdańsk"],
    "Belgrade-Subotica": ["Belgrade", 36, "Novi Sad", 40, "Subotica"],
    "Moscow-Saint Petersburg": ["Moscow", 63, "Tver", 160, "Saint Petersburg"],
    "Ankara-Istanbul": ["Istanbul", 160, "Eskişehir", 85, "Ankara"],
    "Ankara-Konya": ["Ankara", 105, "Konya"],
    "Ankara-Sivas": ["Ankara", 125, "Sivas"],
    "Al Boraq": ["Tangier", 50, "Kenitra", 25, "Rabat", 50, "Casablanca"],
    "Haramain": ["Mecca", 50, "Jeddah", 115, "Medina"],
    "Afrosiyob": ["Tashkent", 130, "Samarkand", 100, "Bukhara"],
    "Beijing-Shanghai": ["Beijing", 33, "Tianjin", 60, "Jinan", 70, "Xuzhou", 70, "Nanjing", 33, "Changzhou", 14,
                         "Wuxi", 12, "Suzhou", 23, "Shanghai"],
    "Beijing-Guangzhou-Shenzhen-Hong Kong": ["Beijing", 70, "Shijiazhuang", 90, "Zhengzhou", 110, "Wuhan", 75,
                                             "Changsha", 135, "Guangzhou", 30, "Shenzhen", 18, "Hong Kong"],
    "Shanghai-Kunming": ["Shanghai", 45, "Hangzhou", 140, "Nanchang", 85, "Changsha", 165, "Guiyang", 120, "Kunming"],
    "Nanjing-Hefei-Wuhan": ["Nanjing", 55, "Hefei", 115, "Wuhan"],
    "Nanjing-Hangzhou": ["Nanjing", 65, "Hangzhou"],
    "Xuzhou-Lanzhou": ["Xuzhou", 90, "Zhengzhou", 30, "Luoyang", 80, "Xi'an", 175, "Lanzhou", 60, "Xining"],
    "Xi'an-Chengdu-Chongqing-Guiyang": ["Xi'an", 185, "Chengdu", 62, "Chongqing", 120, "Guiyang"],
    "Beijing-Harbin": ["Beijing", 160, "Shenyang", 65, "Changchun", 60, "Harbin"],
    "Shenyang-Dalian": ["Shenyang", 105, "Dalian"],
    "Southeast Coast": ["Hangzhou", 50, "Ningbo", 95, "Wenzhou", 95, "Fuzhou", 60, "Xiamen", 90, "Shantou", 100,
                        "Shenzhen"],
    "Nanning-Guangzhou": ["Guangzhou", 190, "Nanning"],
    "Jinan-Qingdao": ["Jinan", 115, "Qingdao"],
    "Shijiazhuang-Jinan": ["Shijiazhuang", 100, "Jinan"],
    "Shijiazhuang-Taiyuan": ["Shijiazhuang", 65, "Taiyuan"],
    "Beijing-Zhangjiakou": ["Beijing", 50, "Zhangjiakou"],
    "Hainan Eastern Ring": ["Haikou", 90, "Sanya"],
    "Tōkaidō and Sanyō Shinkansen": ["Tokyo", 60, "Shizuoka", 23, "Hamamatsu", 30, "Nagoya", 34, "Kyoto", 14,
                                     "Osaka", 45, "Okayama", 35, "Hiroshima", 45, "Kitakyushu", 16, "Fukuoka"],
    "Kyushu Shinkansen": ["Fukuoka", 33, "Kumamoto", 44, "Kagoshima"],
    "Tōhoku and Hokkaido Shinkansen": ["Tokyo", 48, "Utsunomiya", 32, "Kōriyama", 38, "Sendai", 40, "Morioka", 60,
                                       "Aomori", 62, "Hakodate"],
    "Jōetsu Shinkansen": ["Tokyo", 48, "Takasaki", 60, "Niigata"],
    "Hokuriku Shinkansen": ["Takasaki", 32, "Nagano", 48, "Toyama", 22, "Kanazawa", 25, "Fukui"],
    "Gyeongbu HSR": ["Seoul", 58, "Daejeon", 47, "Daegu", 28, "Ulsan", 18, "Busan"],
    "Honam HSR": ["Seoul", 95, "Gwangju", 25, "Mokpo"],
    "Gangneung Line": ["Seoul", 95, "Gangneung"],
    "Taiwan HSR": ["Taipei", 32, "Hsinchu", 27, "Taichung", 30, "Chiayi", 16, "Tainan", 14, "Kaohsiung"],
    "Whoosh": ["Jakarta", 46, "Bandung"],
    "Northeast Corridor (Acela)": ["Boston", 34, "Providence", 70, "New Haven", 95, "New York", 70, "Philadelphia",
                                   62, "Baltimore", 32, "Washington"],
}

# Under construction or planned, with the journey times promised
PLANNED_LINES: dict[str, list[str | int]] = {
    "Rail Baltica": ["Tallinn", 102, "Riga", 95, "Kaunas", 105, "Białystok", 100, "Warsaw"],
    "Rail Baltica to Vilnius": ["Kaunas", 52, "Vilnius"],
    "HS2": ["London", 49, "Birmingham"],
    "Chūō Shinkansen": ["Tokyo", 40, "Nagoya"],
    "Budapest-Belgrade": ["Budapest", 100, "Subotica"],
    "Lisbon-Porto": ["Lisbon", 75, "Porto"],
    "Brescia-Verona-Padua": ["Brescia", 38, "Verona", 45, "Padua"],   # today's times, on the old line
    "Mumbai-Ahmedabad": ["Mumbai", 127, "Ahmedabad"],
}

# Points along the way (lat, lon) for the links that bend, from the first city named to the second
VIA: dict[tuple[str, str], list[tuple[float, float]]] = {
    ("Paris", "Lyon"): [(47.6, 3.5), (46.77, 4.50)],
    ("Lyon", "Avignon"): [(44.99, 4.98)],
    ("Avignon", "Marseille"): [(43.455, 5.317)],
    ("Lyon", "Nîmes"): [(44.99, 4.98), (44.2, 4.75)],
    ("London", "Lille"): [(51.14, 0.88), (51.10, 1.13), (50.90, 1.81)],
    ("Lille", "Paris"): [(49.86, 2.83)],
    ("Brussels", "Antwerp"): [(51.02, 4.48)],
    ("Antwerp", "Rotterdam"): [(51.53, 4.53)],
    ("Rotterdam", "Amsterdam"): [(52.31, 4.76)],
    ("Brussels", "Liège"): [(50.88, 4.72)],
    ("Aachen", "Cologne"): [(50.81, 6.48)],
    ("Paris", "Reims"): [(49.21, 3.99)],
    ("Reims", "Strasbourg"): [(48.95, 6.17), (48.67, 7.71)],
    ("Paris", "Dijon"): [(47.62, 4.34)],
    ("Besançon", "Mulhouse"): [(47.59, 6.90)],
    ("Paris", "Tours"): [(48.08, 1.15), (47.82, 1.03)],
    ("Paris", "Le Mans"): [(48.08, 1.15)],
    ("Le Mans", "Rennes"): [(48.07, -0.77)],
    ("Perpignan", "Girona"): [(42.46, 2.86), (42.27, 2.94)],
    ("Madrid", "Zaragoza"): [(40.59, -3.16), (41.35, -1.64)],
    ("Córdoba", "Málaga"): [(37.02, -4.56)],
    ("Madrid", "Cuenca"): [(40.25, -2.95)],
    ("Cuenca", "Valencia"): [(39.48, -1.10)],
    ("Valladolid", "León"): [(42.01, -4.53)],
    ("León", "Oviedo"): [(42.95, -5.75), (43.16, -5.82)],
    ("Segovia", "Zamora"): [(41.29, -4.68), (41.31, -4.91)],
    ("Zamora", "Ourense"): [(42.05, -6.63), (42.06, -7.14)],
    ("Santiago de Compostela", "Vigo"): [(42.42, -8.64)],
    ("Turin", "Milan"): [(45.45, 8.63)],
    ("Milan", "Bologna"): [(44.73, 10.67)],
    ("Florence", "Rome"): [(43.45, 11.85), (42.72, 12.11)],
    ("Rome", "Naples"): [(41.55, 13.45)],
    ("Milan", "Brescia"): [(45.52, 9.59)],
    ("Verona", "Padua"): [(45.54, 11.54)],
    ("Padua", "Venice"): [(45.48, 12.23)],
    ("Cologne", "Frankfurt"): [(50.79, 7.20), (50.44, 7.83), (50.38, 8.10)],
    ("Mannheim", "Stuttgart"): [(48.93, 8.96)],
    ("Stuttgart", "Ulm"): [(48.51, 9.75)],
    ("Munich", "Nuremberg"): [(48.74, 11.43)],
    ("Nuremberg", "Erfurt"): [(49.90, 10.90), (50.26, 10.96)],
    ("Erfurt", "Leipzig"): [(51.33, 11.75)],
    ("Leipzig", "Berlin"): [(51.87, 12.66)],
    ("Hamburg", "Berlin"): [(53.33, 11.50), (53.0, 11.75)],
    ("Wolfsburg", "Berlin"): [(52.60, 11.86), (52.53, 13.20)],
    ("Hamburg", "Hanover"): [(53.25, 10.42), (52.97, 10.55), (52.62, 10.06)],
    ("Fulda", "Frankfurt"): [(50.12, 8.93)],
    ("Karlsruhe", "Freiburg"): [(48.48, 7.95)],
    ("Bern", "Zurich"): [(47.35, 7.91)],
    ("Zurich", "Lugano"): [(47.05, 8.55), (46.83, 8.65), (46.38, 8.91), (46.20, 9.02)],
    ("Lugano", "Milan"): [(45.83, 9.03), (45.81, 9.07)],
    ("Vienna", "Linz"): [(48.21, 15.62)],
    ("Linz", "Salzburg"): [(48.17, 14.03)],
    ("Graz", "Klagenfurt"): [(46.83, 15.38), (46.76, 14.85)],
    ("Warsaw", "Kraków"): [(51.6, 20.4), (50.85, 19.97)],
    ("Warsaw", "Katowice"): [(51.6, 20.4), (50.85, 19.97), (50.49, 19.42)],
    ("Warsaw", "Gdańsk"): [(53.24, 20.18), (53.6, 19.57), (54.03, 19.03)],
    ("Tver", "Saint Petersburg"): [(57.88, 34.05), (59.12, 31.66)],
    ("Istanbul", "Eskişehir"): [(40.78, 29.94), (40.15, 29.98)],
    ("Eskişehir", "Ankara"): [(39.58, 32.14)],
    ("Ankara", "Konya"): [(39.58, 32.14)],
    ("Ankara", "Sivas"): [(39.84, 33.51), (39.82, 34.80)],
    ("Jeddah", "Medina"): [(22.40, 39.10), (23.50, 39.30)],
    ("Tianjin", "Jinan"): [(38.3, 116.85), (37.45, 116.3)],
    ("Jinan", "Xuzhou"): [(35.60, 116.98), (34.86, 117.30)],
    ("Xuzhou", "Nanjing"): [(32.90, 117.38), (32.30, 118.20)],
    ("Shijiazhuang", "Zhengzhou"): [(36.60, 114.48), (36.10, 114.35), (35.30, 113.90)],
    ("Zhengzhou", "Wuhan"): [(33.58, 114.02), (32.13, 114.07), (31.30, 114.10)],
    ("Changsha", "Guangzhou"): [(26.89, 112.60), (25.80, 113.0), (24.80, 113.60)],
    ("Hangzhou", "Nanchang"): [(29.10, 119.65), (28.95, 118.87), (28.45, 117.97)],
    ("Nanchang", "Changsha"): [(27.82, 114.40), (27.63, 113.85)],
    ("Changsha", "Guiyang"): [(27.70, 111.43), (27.55, 110.00), (26.57, 107.97)],
    ("Guiyang", "Kunming"): [(26.25, 105.93), (25.49, 103.80)],
    ("Hefei", "Wuhan"): [(31.73, 116.50), (31.2, 115.5)],
    ("Luoyang", "Xi'an"): [(34.77, 111.20), (34.52, 110.08)],
    ("Xi'an", "Lanzhou"): [(34.36, 107.24), (34.58, 105.72)],
    ("Xi'an", "Chengdu"): [(33.07, 107.03), (32.44, 105.84), (31.47, 104.68)],
    ("Chongqing", "Guiyang"): [(27.70, 106.90)],
    ("Beijing", "Shenyang"): [(40.97, 117.94), (41.57, 120.45), (42.02, 121.65)],
    ("Ningbo", "Wenzhou"): [(28.66, 121.42)],
    ("Wenzhou", "Fuzhou"): [(26.66, 119.52)],
    ("Fuzhou", "Xiamen"): [(25.43, 119.0), (24.90, 118.58)],
    ("Xiamen", "Shantou"): [(24.51, 117.65), (23.95, 117.30)],
    ("Shantou", "Shenzhen"): [(22.95, 115.36), (23.08, 114.42)],
    ("Guangzhou", "Nanning"): [(23.48, 111.28), (23.10, 109.60)],
    ("Jinan", "Qingdao"): [(36.70, 119.10)],
    ("Shijiazhuang", "Taiyuan"): [(37.86, 113.58)],
    ("Haikou", "Sanya"): [(19.25, 110.47), (18.80, 110.40)],
    ("Tokyo", "Shizuoka"): [(35.256, 139.155), (35.10, 139.08), (35.13, 138.91)],
    ("Osaka", "Okayama"): [(34.705, 135.195), (34.827, 134.69)],
    ("Okayama", "Hiroshima"): [(34.49, 133.36)],
    ("Hiroshima", "Kitakyushu"): [(34.05, 131.81), (34.09, 131.40)],
    ("Kumamoto", "Kagoshima"): [(32.21, 130.42), (31.82, 130.30)],
    ("Tokyo", "Utsunomiya"): [(35.906, 139.624)],
    ("Morioka", "Aomori"): [(40.51, 141.43)],
    ("Aomori", "Hakodate"): [(41.30, 140.30), (41.68, 140.44)],
    ("Tokyo", "Takasaki"): [(35.906, 139.624)],
    ("Takasaki", "Niigata"): [(36.94, 138.81), (37.45, 138.85)],
    ("Takasaki", "Nagano"): [(36.34, 138.63), (36.40, 138.25)],
    ("Nagano", "Toyama"): [(37.08, 138.25), (37.04, 137.86), (36.87, 137.45)],
    ("Daejeon", "Daegu"): [(36.11, 128.18)],
    ("Daegu", "Ulsan"): [(35.80, 129.14)],
    ("Seoul", "Gwangju"): [(36.62, 127.33), (35.94, 126.95)],
    ("Seoul", "Gangneung"): [(37.31, 127.92), (37.56, 128.43)],
    ("Boston", "Providence"): [(42.10, -71.20)],
    ("Providence", "New Haven"): [(41.35, -71.90), (41.30, -72.40)],
    ("New Haven", "New York"): [(41.05, -73.55), (40.85, -73.85)],
    ("Tallinn", "Riga"): [(58.37, 24.52)],
    ("Riga", "Kaunas"): [(55.73, 24.37)],
    ("Kaunas", "Białystok"): [(54.56, 23.35), (53.82, 22.36)],
    ("London", "Birmingham"): [(51.53, -0.25), (51.90, -1.00), (52.45, -1.72)],
    ("Tokyo", "Nagoya"): [(35.63, 139.74), (35.57, 139.35), (35.60, 138.60), (35.50, 137.80)],
    ("Budapest", "Subotica"): [(46.91, 19.69)],
    ("Lisbon", "Porto"): [(39.75, -8.80), (40.22, -8.43), (40.64, -8.64)],
}


class HighSpeedRail(NamedTuple):
    """The links of the lines: each joins cities *a* and *b* (indices into *names*), *minutes* apart."""
    names: list[str]
    lon: np.ndarray
    lat: np.ndarray
    population: np.ndarray
    a: np.ndarray
    b: np.ndarray
    minutes: np.ndarray
    score: np.ndarray
    planned: np.ndarray        # True for a link not yet open
    within: np.ndarray         # True for a link within one urban centre (Guangzhou-Shenzhen), not scored
    lines: list[str]           # the line each link is on
    with_planned: bool
    router: TrackRouter | None = None    # draws the links along OSM's tracks; None draws them city to city


def resolve_high_speed_rail(text: str | bool | None) -> str:
    """``"open"`` (blank or ``"all"`` too) or ``"planned"`` (also ``"all-planned"``). Raises ``ValueError``."""
    if text is True:
        return "open"
    key = str(text or "").strip().lower().replace("_", "-")
    if key in ("", "open", "all", "high-speed-rail", "hsr"):
        return "open"
    if key in ("planned", "all-planned", "proposed", "future"):
        return "planned"
    raise ValueError(f"Unknown high-speed rail choice {text!r}; choose open or planned.")


def gravity(p1: float | np.ndarray, p2: float | np.ndarray, minutes: float | np.ndarray) -> np.ndarray:
    """The gravity score of a link between cities of *p1* and *p2* people, *minutes* apart:
    (P1 × P2 / 100,000) / (2 × minutes)."""
    return np.asarray(p1, float) * np.asarray(p2, float) / DIVISOR / (2 * np.asarray(minutes, float))


def score_class(score: float | np.ndarray) -> np.ndarray:
    """The class (0 to 5) of each gravity *score*, a power of ten apart from 1,000 to 10 million."""
    return np.searchsorted(np.array(BREAKS, float), np.asarray(score, float), side="right")


def format_score(score: float) -> str:
    """A gravity score in a few figures: ``730``, ``27k``, ``4.2M``."""
    if score < 1_000:
        return f"{score:.0f}"
    for unit, size in (("M", 1e6), ("k", 1e3)):
        if score >= size:
            value = score / size
            text = f"{value:.1f}" if value < 10 else f"{value:.0f}"
            return text.removesuffix(".0") + unit
    return f"{score:.0f}"


def _sequence(name: str, items: list[str | int]) -> list[tuple[str, str, int]]:
    if len(items) < 3 or len(items) % 2 == 0:
        raise ValueError(f"High-speed line {name!r} must alternate cities and minutes")
    links = []
    for k in range(0, len(items) - 2, 2):
        a, minutes, b = items[k], items[k + 1], items[k + 2]
        if not isinstance(a, str) or not isinstance(b, str) or not isinstance(minutes, int) or minutes <= 0:
            raise ValueError(f"High-speed line {name!r} must alternate cities and minutes")
        if a not in STATIONS or b not in STATIONS:
            missing = a if a not in STATIONS else b
            raise ValueError(f"High-speed line {name!r} names a city without a station: {missing}")
        links.append((a, b, minutes))
    return links


def load_high_speed_rail(choice: str | bool | None = "open", tracks: bool = True, cache_dir: str | None = None,
                         fetch: Any = None) -> HighSpeedRail:
    """The links of the high-speed lines, open or (``"planned"``) open and planned, each scored. A pair of cities
    on two lines is one link, the faster.

    With *tracks* the links in view are drawn along OSM's tracks, downloaded as they are needed into
    *cache_dir* (see :mod:`rail_tracks`; *fetch* stands in for the Overpass API); without, city to city.
    """
    with_planned = resolve_high_speed_rail(choice) == "planned"
    names = list(STATIONS)
    index = {n: i for i, n in enumerate(names)}
    best: dict[frozenset[str], tuple[str, str, int, bool, str]] = {}
    tables = [(OPEN_LINES, False)] + ([(PLANNED_LINES, True)] if with_planned else [])
    for table, planned in tables:
        for line, items in table.items():
            for a, b, minutes in _sequence(line, items):
                key = frozenset((a, b))
                known = best.get(key)
                # An open link stands for the pair; a planned one only where none is open, or faster
                if known is None or (known[3] == planned and minutes < known[2]):
                    best[key] = (a, b, minutes, planned, line)
    links = list(best.values())
    lat = np.array([STATIONS[n][0] for n in names])
    lon = np.array([STATIONS[n][1] for n in names])
    population = np.array([STATIONS[n][2] for n in names], float)
    centres = [STATIONS[n][3] for n in names]
    a = np.array([index[l[0]] for l in links], int)
    b = np.array([index[l[1]] for l in links], int)
    minutes = np.array([l[2] for l in links], int)
    within = np.array([bool(centres[i]) and centres[i] == centres[j] for i, j in zip(a, b)], bool)
    score = gravity(population[a], population[b], minutes) if links else np.zeros(0)
    score[within] = 0.0
    # The lowest first, so the strongest links are drawn on top
    order = np.argsort(score, kind="stable")
    logger.info("High-speed rail: %d links between %d cities%s.", len(links), len({*a, *b}),
                ", with those planned" if with_planned else "")
    return HighSpeedRail(names, lon, lat, population, a[order], b[order], minutes[order], score[order],
                         np.array([l[3] for l in links], bool)[order], within[order], [links[i][4] for i in order],
                         with_planned, _router(names, cache_dir, fetch) if tracks else None)


def _router(names: list[str], cache_dir: str | None, fetch: Any) -> TrackRouter:
    return TrackRouter({n: (STATIONS[n][1], STATIONS[n][0]) for n in names}, cache_dir, fetch)


def high_speed_rail_attribution(rail: HighSpeedRail) -> str:
    """The credit for *rail*, OSM's among them once a link was drawn along its tracks."""
    if rail.router is not None and rail.router.used:
        return f"{HIGH_SPEED_RAIL_ATTRIBUTION}; {TRACKS_ATTRIBUTION}"
    return HIGH_SPEED_RAIL_ATTRIBUTION


# ---------------------------------------------------------------------------
# Drawing
# ---------------------------------------------------------------------------


def _width_scale(ax: GeoAxes) -> float:
    """Line widths grow with the map, as on a 15-inch globe."""
    return float(np.clip(ax.get_window_extent().width / ax.figure.dpi / 15, 0.4, 3))


def _path(rail: HighSpeedRail, k: int) -> tuple[np.ndarray, np.ndarray]:
    """The points (lon, lat) of link *k*, along great circles through its points along the way."""
    a, b = int(rail.a[k]), int(rail.b[k])
    na, nb = rail.names[a], rail.names[b]
    via = VIA.get((na, nb))
    if via is None:
        via = list(reversed(VIA.get((nb, na), [])))
    points = [(float(rail.lat[a]), float(rail.lon[a]))] + list(via) + [(float(rail.lat[b]), float(rail.lon[b]))]
    lons, lats = [], []
    for (lat1, lon1), (lat2, lon2) in zip(points, points[1:]):
        lon, lat = great_circle(lon1, lat1, lon2, lat2, step=0.25)
        lons.append(lon if not lons else lon[1:])
        lats.append(lat if not lats else lat[1:])
    return np.concatenate(lons), np.concatenate(lats)


def _runs(xy: np.ndarray, ok: np.ndarray) -> list[np.ndarray]:
    """The runs of points of *xy* where *ok* holds, two points or more."""
    edges = np.flatnonzero(np.diff(np.concatenate([[0], ok.astype(int), [0]])))
    return [xy[s:e] for s, e in zip(edges[::2], edges[1::2]) if e - s >= 2]


def _pieces(ax: GeoAxes, lon: np.ndarray, lat: np.ndarray) -> list[np.ndarray]:
    """The parts of a path (map coordinates) on the near side of the globe."""
    xy = ax.projection.transform_points(ccrs.PlateCarree(), lon, lat)[:, :2]
    ok = near_side(ax, lon, lat, xy)
    return _runs(xy, ok) if ok.any() else []


def _middle(piece: np.ndarray) -> tuple[float, float, float, int]:
    """The point halfway along a path (map coordinates), the path's length, and the step the point is on."""
    steps = np.hypot(*np.diff(piece, axis=0).T)
    along = np.concatenate([[0], np.cumsum(steps)])
    half = along[-1] / 2
    k = int(np.clip(np.searchsorted(along, half) - 1, 0, len(steps) - 1))
    t = (half - along[k]) / steps[k] if steps[k] > 0 else 0.0
    x, y = piece[k] + t * (piece[k + 1] - piece[k])
    return float(x), float(y), float(along[-1]), k


def draw_high_speed_rail(ax: GeoAxes, rail: HighSpeedRail, labels: bool = True,
                         limit: int = LABEL_MAX, score_limit: int = SCORE_LABEL_MAX) -> set[int]:
    """Draw the links of *rail* on *ax*, wider and darker the higher their gravity score (planned ones dashed),
    the cities on them dotted, the largest named and the highest scores written along their links. Returns the
    links that show (indices into *rail*)."""
    scale = _width_scale(ax)
    classes = score_class(rail.score)
    paths = {k: _path(rail, k) for k in range(len(rail.a))}
    if rail.router is not None:
        # The links near the map, drawn city to city, are drawn along OSM's tracks where they can be
        near = [k for k, path in paths.items()
                if any(frame.inside(ax, p[:, 0], p[:, 1], margin=1.2).any() for p in _pieces(ax, *path))]
        rail.router.prepare(paths[k] for k in near)
        for k in near:
            track = rail.router.route(rail.names[rail.a[k]], rail.names[rail.b[k]], bool(rail.planned[k]),
                                      *paths[k])
            if track is not None:
                paths[k] = track
    shown: list[tuple[int, np.ndarray]] = []         # the parts on the near side, drawn (the map clips them)
    seen: list[tuple[int, np.ndarray]] = []          # the parts on the map, for the count and the scores
    for k, path in paths.items():
        for piece in _pieces(ax, *path):
            on_map = frame.inside(ax, piece[:, 0], piece[:, 1])
            if on_map.any():
                shown.append((k, piece))
                seen += [(k, part) for part in _runs(piece, on_map)]
    if not shown:
        logger.info("High-speed rail drawn: none of %d links in view.", len(rail.a))
        return set()
    for planned in (False, True):
        group = [(k, p) for k, p in shown if bool(rail.planned[k]) == planned]
        if not group:
            continue
        widths = [(WIDTHS[0] if rail.within[k] else WIDTHS[classes[k]]) * scale for k, _ in group]
        style = (0, (2.2, 1.4)) if planned else "solid"
        # A pale casing under the open lines, so they stand out on any map
        if not planned:
            ax.add_collection(LineCollection([p for _, p in group], linewidths=[w + 1.2 * scale for w in widths],
                                             colors="white", alpha=0.7, capstyle="round", joinstyle="round",
                                             zorder=7.55))
        ax.add_collection(LineCollection([p for _, p in group], linewidths=widths,
                                         colors=[WITHIN_COLOUR if rail.within[k] else COLOURS[classes[k]]
                                                 for k, _ in group], linestyles=style,
                                         capstyle="butt" if planned else "round", joinstyle="round",
                                         zorder=7.6 + (0.01 if not planned else 0)))
    links = sorted({k for k, _ in seen})
    _draw_stations(ax, rail, sorted({k for k, _ in shown}), scale, labels, limit)
    if labels:
        _draw_scores(ax, rail, seen, scale, score_limit)
    logger.info("High-speed rail drawn: %d of %d links in view.", len(links), len(rail.a))
    return set(links)


def _centre(name: str) -> str:
    """The GHSL urban centre city *name*'s station is in ("" for none)."""
    return STATIONS[name][3] if name in STATIONS else ""


def _draw_stations(ax: GeoAxes, rail: HighSpeedRail, links: list[int], scale: float, labels: bool,
                   limit: int) -> None:
    cities = sorted({int(rail.a[k]) for k in links} | {int(rail.b[k]) for k in links},
                    key=lambda i: (-rail.population[i], _centre(rail.names[i]) != rail.names[i]))
    seen = [i for i in cities if on_globe(ax, float(rail.lon[i]), float(rail.lat[i]), margin=1.0)]
    if not seen:
        return
    sizes = np.array([10 + 5 * np.sqrt(rail.population[i] / 1e6) for i in seen]).clip(10, 45) * scale
    lon = np.array([rail.lon[i] for i in seen])
    lat = np.array([rail.lat[i] for i in seen])
    ax.scatter(lon, lat, s=sizes, c=STATION_COLOUR, edgecolors="#3f007d", linewidths=0.6 * scale,
               transform=ccrs.PlateCarree(), zorder=7.8)
    if not labels:
        return
    from cities import side_offset
    space = label_space(ax)
    named = 0
    for i, size in zip(seen, sizes):
        if named >= limit:
            break
        x, y = ax.projection.transform_point(float(rail.lon[i]), float(rail.lat[i]), ccrs.PlateCarree())
        font = float(np.clip(7 + 0.9 * np.log2(max(rail.population[i], 1) / 1e6), 6.5, 11)) * scale ** 0.5
        name = rail.names[i]
        side = space.claim_around(x, y, name, font, point=np.sqrt(size) * ax.figure.dpi / 72, bold=True)
        if side is None:
            continue
        named += 1
        ax.annotate(name, (x, y), xycoords="data", textcoords="offset points", fontsize=font, fontweight="bold",
                    color="#222222", zorder=8, annotation_clip=True, **side_offset(side, font))


def _draw_scores(ax: GeoAxes, rail: HighSpeedRail, shown: list[tuple[int, np.ndarray]], scale: float,
                 limit: int) -> None:
    """Write the highest scores along their links, turned to run with them, where they fit."""
    space = label_space(ax)
    dpi_scale = ax.figure.dpi / 72
    longest: dict[int, np.ndarray] = {}
    for k, piece in shown:
        if rail.within[k]:
            continue
        if k not in longest or len(piece) > len(longest[k]):  # the longest part on the map
            longest[k] = piece
    written = 0
    for k in sorted(longest, key=lambda k: -rail.score[k]):
        if written >= limit:
            break
        text = format_score(float(rail.score[k]))
        font = 6.5 * scale ** 0.5
        piece = longest[k]
        x, y, _, at = _middle(piece)
        pixels = ax.transData.transform(piece)
        length = float(np.hypot(*np.diff(pixels, axis=0).T).sum())
        width, height = (0.62 * font * len(text) + 0.6 * font) * dpi_scale, 1.3 * font * dpi_scale
        if length < 1.6 * width:
            continue
        p0, p1 = ax.transData.transform(piece[at]), ax.transData.transform(piece[min(at + 1, len(piece) - 1)])
        angle = math.degrees(math.atan2(p1[1] - p0[1], p1[0] - p0[0]))
        if angle > 90:
            angle -= 180
        elif angle < -90:
            angle += 180
        px, py = ax.transData.transform((x, y))
        c, s = abs(math.cos(math.radians(angle))), abs(math.sin(math.radians(angle)))
        half_w, half_h = (width * c + height * s) / 2, (width * s + height * c) / 2
        box = (px - half_w, py - half_h, px + half_w, py + half_h)
        if not frame.box_inside(ax, box, 0.97):
            continue
        if any(box[0] < q[2] and q[0] < box[2] and box[1] < q[3] and q[1] < box[3] for q in space.placed):
            continue
        space.placed.append(box)
        written += 1
        colour = COLOURS[int(score_class(rail.score[k]))]
        ax.text(x, y, text, rotation=angle, rotation_mode="anchor", ha="center", va="center", fontsize=font,
                fontweight="bold", color="#3f007d", zorder=8.1, clip_on=True,
                bbox={"boxstyle": "round,pad=0.18", "facecolor": "white", "edgecolor": colour,
                      "linewidth": 0.6 * scale, "alpha": 0.9})


def add_high_speed_rail_legend(ax: GeoAxes, rail: HighSpeedRail, shown: set[int], y: float = -0.01,
                               x: float = 0.5) -> Legend:
    """A key to the gravity-score classes and, of the links *shown*, those within one city and the planned ones'
    dashes."""
    scale = _width_scale(ax)
    handles, labels = [], []
    bounds = (0,) + BREAKS + (None,)
    for k, colour in enumerate(COLOURS):
        low, high = bounds[k], bounds[k + 1]
        label = (f"under {format_score(high)}" if k == 0 else
                 f"{format_score(low)} or more" if high is None else f"{format_score(low)}-{format_score(high)}")
        handles.append(Line2D([], [], color=colour, linewidth=WIDTHS[k] * scale, solid_capstyle="butt"))
        labels.append(label)
    if any(rail.within[k] for k in shown):
        handles.append(Line2D([], [], color=WITHIN_COLOUR, linewidth=WIDTHS[0] * scale))
        labels.append("within one urban area")
    if any(rail.planned[k] for k in shown):
        handles.append(Line2D([], [], color=COLOURS[3], linewidth=WIDTHS[3] * scale, linestyle=(0, (2.2, 1.4))))
        labels.append("planned")
    title = (f"High-speed rail: {len(shown):,} links in view, by gravity score = (P₁ × P₂ / 100,000) / "
             "(2 × minutes): people in the two cities (GHSL 2025), fastest train")
    legend = Legend(ax, handles, labels, loc="upper center", bbox_to_anchor=(x, y), ncol=min(len(handles), 8),
                    fontsize=10, title=title, title_fontsize=10, frameon=True, fancybox=True, framealpha=0.85,
                    edgecolor="#444444", handlelength=2.5, columnspacing=1.6, borderpad=0.7)
    legend.set_clip_on(False)
    ax.add_artist(legend)
    return legend

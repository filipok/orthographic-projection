# Orthographic Map Generator

This project generates high-resolution orthographic globe images centered on a selected major city using Cartopy, Matplotlib, and online web map tiles.

The main script is [ortho.py](ortho.py).

## Sample Output

<p align="center">
  <img src="sample_sao_paulo.png" alt="Orthographic globe centred on São Paulo with 2,500 km and 5,000 km distance circles" width="48%">
  <img src="sample_london_koppen.png" alt="Orthographic globe centred on London with Köppen-Geiger climate overlay" width="48%">
</p>

*Left: Orthographic globe centred on São Paulo (Google Satellite, zoom 3) showing concentric geodesic distance circles.*<br>
*Right: Orthographic globe centred on London (OSM, zoom 3) featuring the Köppen-Geiger climate classification overlay.*

<p align="center">
  <img src="sample_portuguese_voyages.png" alt="Satellite globe centred on Lisbon with the Portuguese voyages of discovery drawn in eight colours and a key naming each voyage" width="48%">
  <img src="sample_viking_routes.png" alt="Globe centred on Scandinavia with Viking homelands, settlements, trade routes, raids and exploration voyages, Arctic sea ice at its winter maximum, and a key" width="48%">
</p>

*Left: The Portuguese voyages of discovery, 1415–1522, centred on Lisbon (Google Satellite, zoom 3) with a route key.*<br>
*Right: Viking Age homelands, settlements, trade, raids and exploration, centred on Scandinavia (OSM, zoom 3) with a route key and polar ice at its winter maximum.*

<p align="center">
  <img src="sample_portuguese_hemispheres.png" alt="Two satellite globes side by side: Lisbon's hemisphere with the Portuguese voyages, and the opposite hemisphere centred on Lisbon's antipode showing Magellan's Pacific crossing and Elcano's return" width="97%">
</p>

*Both hemispheres: Lisbon's globe and, beside it, the globe centred on its antipode in the Tasman Sea, which shows Magellan's Pacific crossing and Elcano's return across the Indian Ocean (Google Satellite, zoom 3).*

<p align="center">
  <img src="sample_tokyo_lisbon.png" alt="Two globes side by side on a plain map, centred on Tokyo and on Lisbon, with arrows for the year's prevailing winds, the trade winds blowing steadily westward across the tropical Pacific and Atlantic, polar ice over the Arctic Ocean and Greenland, wheat in gold across China, India, Europe, Russia and Canada, and rice in teal across south and east Asia and west Africa, with keys for the crops and winds" width="97%">
</p>

*Two places side by side: Tokyo and Lisbon on the plain OSM map, with the year's prevailing winds, polar ice at its winter maximum, and wheat or rice, whichever covers more land (zoom 3).*

<p align="center">
  <img src="sample_zheng_he_voyages.png" alt="Satellite map of the Indian Ocean within 5,500 km of southern India with the voyages of Zheng He: a red route from Nanjing down the Chinese coast past Champa, Java, Palembang, Malacca and Sumatra to Ceylon and Calicut; yellow on to Hormuz; green along Arabia to Aden and across to Mogadishu, Brava and Malindi; white squadrons to Siam, Bengal and Jeddah for Mecca; Köppen-Geiger deserts and steppes shaded over Arabia, Iran and Central Asia, dry-winter continental climates in northeast China and tundra and ice cap on the Tibetan Plateau; dashed circles 2,500, 5,000 and 7,500 km from Nanjing; and keys" width="60%">
</p>

*The voyages of Zheng He's treasure fleets, 1405–1433: the route from Nanjing to Calicut that all seven voyages sailed, and the later voyages on to Hormuz, Arabia and East Africa, over the Köppen-Geiger arid (B), dry-winter continental (Dw) and polar (E) climates, with distance circles from Nanjing (NASA Blue Marble, a 5,500 km radius map at zoom 5).*

<p align="center">
  <img src="sample_london_wheat.png" alt="Globe centred on London with wheat-growing areas shaded gold, from Europe and the Black Sea to Kazakhstan, the Middle East and Ethiopia" width="48%">
  <img src="sample_shanghai_wheat_rice.png" alt="Globe centred on Shanghai with wheat in gold and rice in teal, showing wheat in northern China and north-west India and rice to the south and east" width="48%">
</p>

*Left: Where wheat is grown, centred on London (OSM, zoom 3), c. 2020.*<br>
*Right: Wheat or rice, whichever covers more land in each place, centred on Shanghai (OSM, zoom 3): the wheat–rice divide in China and India.*

<p align="center">
  <img src="sample_nyc_toward_london.png" alt="NASA Blue Marble globe centred on New York and turned so the direction toward London points up, with Europe along the top" width="48%">
  <img src="sample_lusaka_cwa.png" alt="Globe centred on Lusaka showing only the Cwa humid subtropical dry-winter climate, a band from Angola across Zambia to Mozambique" width="48%">
</p>

*Left: Centred on New York and turned so London's direction (bearing 51°) is up (NASA Blue Marble, zoom 3).*<br>
*Right: Only Cwa, humid subtropical with dry winters, which covers 71% of Zambia, centred on Lusaka (OSM, zoom 3).*

<p align="center">
  <img src="sample_himalaya_trewartha.png" alt="Globe centred over the Himalaya with the Trewartha climate classification: tropical wet Indonesia, tropical India, deserts from the Sahara to the Gobi, subtropical south China, temperate and boreal Russia, and the Tibetan Plateau in the brown highland class" width="48%">
  <img src="sample_andes_elevation.png" alt="Globe centred on South America with land coloured by height from green lowlands to brown and white peaks, hill-shaded, showing the Andes, the Brazilian Highlands and the Mexican Plateau" width="48%">
</p>

*Left: The Trewartha climate classification across Asia, centred over the Himalaya (OSM, zoom 3), with the Tibetan Plateau in the highland group (H).*<br>
*Right: Elevation, coloured by height and hill-shaded, centred on South America (OSM, zoom 3).*

<p align="center">
  <img src="sample_steppe_soils.png" alt="Globe centred on Kazakhstan showing only the steppe soils: black Chernozems from Romania through Ukraine and southern Russia into Siberia, chestnut Kastanozems to the south across Kazakhstan and Mongolia, and Phaeozems along the northern edge" width="48%">
  <img src="sample_topsoil_ph.png" alt="Globe centred on the Sahara showing topsoil pH: acid soils in Scandinavia, Britain and the Congo Basin, alkaline soils across the deserts from the Sahara to Central Asia, and strongly alkaline soils in the Kalahari" width="48%">
</p>

*Left: The steppe's dark grassland soils, Chernozems, Kastanozems and Phaeozems, centred on Kazakhstan (OSM, zoom 3, SoilGrids 2.0).*<br>
*Right: Topsoil pH (0–5 cm): acid under the northern and tropical forests, alkaline under the deserts (OSM, zoom 3, SoilGrids 2.0).*

<p align="center">
  <img src="sample_africa_biomes.png" alt="Globe centred on Africa on a plain map, coloured by biome: the Congo rainforest in green ringed by orange savannas, the Sahara, Arabia and the Namib in desert pink, red Mediterranean scrub along the Maghreb, southern Europe and the Cape, temperate forests across Europe, steppe in Ukraine and Kazakhstan, with a key naming the biomes" width="48%">
  <img src="sample_madagascar_ecoregions.png" alt="A disc 1,200 km around Madagascar coloured by ecoregion: humid forests down the east coast, subhumid forests over the central highlands, dry deciduous forests in the west, succulent woodlands and spiny thickets in the south, mangroves on the west coast, miombo woodlands and coastal forests in Mozambique, and the Comoros and Mascarene forests, with a key naming the largest" width="48%">
</p>

*Left: Africa's biomes, from the Congo rainforest through the savannas to the Sahara, the Namib and the Mediterranean scrub of the Cape and the Maghreb (plain OSM, zoom 3, RESOLVE Ecoregions 2017).*<br>
*Right: Madagascar's ecoregions within 1,200 km: rainforest down the east, dry forest in the west, spiny thickets in the south (plain OSM, zoom 8, RESOLVE Ecoregions 2017).*

<p align="center">
  <img src="sample_borders_1500.png" alt="Globe centred on Lisbon on a plain map with the states of 1500 in muted colours, outlined and named: the Crown of Castile, the Kingdom of France, the Holy Roman Empire, the House of Jagiellon, the Grand Principality of Moscow, the Kalmar Union, the Ottoman Empire, the Mamluk Sultanate, Aq Qoyunlu, the Timurid Empire, the Kazakh Khanate and the Khanate of Sibir, the Wattasid dynasty, the Songhai and Mali Empires; with the Portuguese voyages of discovery drawn over them, down the African coast, round the Cape to India and across to Brazil, and a key naming the largest states" width="48%">
  <img src="sample_africa_1914.png" alt="Globe centred on Africa on a plain map with the borders of 1 July 1914: the colonies of France, Britain, Germany, Belgium, Portugal and Italy each in a lighter shade of its ruler's colour, Ethiopia and Liberia independent, the Ottoman Empire, Persia, Russia and the states of Europe, with a key naming the largest" width="48%">
</p>

*Left: The states of 1500 with the Portuguese voyages of discovery, centred on Lisbon (plain OSM, zoom 3, Cliopatria).*<br>
*Right: Africa on 1 July 1914, each colony in a lighter shade of its ruler's colour (plain OSM, zoom 3, CShapes 2.0).*

<p align="center">
  <img src="sample_ice_age_north_sea.png" alt="A disc 2,500 km around the North Sea with the sea floor coloured by depth, from pale shelves to the deep blue Atlantic and Norwegian Sea, the land the last ice age left dry in sand: Doggerland joining Britain to the continent, the English Channel, the northern Adriatic and the Sea of Azov, and the ice sheet of 20,000 years ago in white over Scandinavia, the Baltic, Scotland and Iceland" width="48%">
  <img src="sample_ring_of_fire.png" alt="Globe centred on the western Pacific with the plate boundaries, red spreading ridges, blue subduction zones and green transform faults, the earthquakes of magnitude 6 and more since 1900 in orange, yellow and purple by depth, deep ones behind every trench, and red triangles for volcanoes that have erupted since 1900, along Japan, the Philippines, Indonesia and New Zealand" width="48%">
</p>

*Left: The North Sea in the last ice age: Doggerland, the ice sheets of 20,000 years ago and the sea floor by depth (plain OSM, radius 2,500 km, PaleoMIST).*<br>
*Right: The Ring of Fire: plate boundaries, earthquakes since 1900 by depth, and volcanoes (plain OSM, zoom 3).*

<p align="center">
  <img src="sample_atlantic_currents.png" alt="Globe centred on the Atlantic with the ocean surface currents as arrows, warm red ones in the Gulf Stream and North Atlantic Drift, cold blue ones in the Labrador, Canary, Benguela and Humboldt currents, and the hurricane tracks since 1980 coloured by category, sweeping west across the tropical Atlantic and curving north" width="48%">
  <img src="sample_africa_basins.png" alt="Globe centred on Africa with the river basins coloured and named, the Congo, Nile, Niger, Zambezi, Orange, Volta and Limpopo, the Sahara and Arabia's basins of inland drainage in grey, and the rivers in blue" width="48%">
</p>

*Left: The Atlantic's surface currents, warm and cold, with the hurricane tracks since 1980 (plain OSM, zoom 3).*<br>
*Right: Africa's river basins and rivers, inland drainage in grey (plain OSM, zoom 3, HydroBASINS).*

<p align="center">
  <img src="sample_asia_population.png" alt="Globe centred on the Bay of Bengal with population density in 2020, from pale yellow under 1 person per km² across Siberia, Tibet and Australia to deep red over 1,000 along the Ganges plain, eastern China, Java and the Nile" width="48%">
  <img src="sample_india_cattle.png" alt="Globe centred on India with the density of cattle in 2015, darkest across India and Bangladesh, the Ethiopian highlands and East Africa, lighter across China and Europe, and none in the deserts" width="48%">
</p>

*Left: Population density in 2020 (plain OSM, zoom 3, GPWv4).*<br>
*Right: Cattle per km², 2015 (plain OSM, zoom 3, Gridded Livestock of the World).*

<p align="center">
  <img src="sample_africa_malaria.png" alt="Globe centred on Africa with the Plasmodium falciparum parasite rate in children in 2025, deep red over 40% across the Congo basin, Angola, Zambia and parts of West Africa, orange across the Sahel and Mozambique, pale yellow in Namibia, the Horn of Africa and western India, and none in North Africa, Europe or South Africa" width="48%">
  <img src="sample_americas_aedes.png" alt="Globe centred on the Americas with red dots where Aedes aegypti was recorded, thickest in eastern Brazil and spread through Central America, the Caribbean, Mexico and the southern United States, and blue dots for Aedes albopictus across the eastern United States, southern Brazil and southern Europe" width="48%">
</p>

*Left: Malaria (P. falciparum) in children aged 2–10, 2025 (plain OSM, zoom 3, Malaria Atlas Project).*<br>
*Right: Where the dengue mosquitoes Aedes aegypti and Ae. albopictus were recorded, 1958–2014 (plain OSM, zoom 3, Kraemer et al. 2015).*

<p align="center">
  <img src="sample_africa_lion.png" alt="Globe centred on Africa with pink to purple squares where lions have been recorded in the wild, thickest in Kenya, Tanzania, the Okavango, Zimbabwe and Kruger, scattered across the Sahel and West Africa, and a small cluster in Gujarat in India" width="48%">
  <img src="sample_britain_red_fox.png" alt="A disc 900 km around southern England with squares of about 20 km where red foxes were recorded since 2000: darkest over England, the Netherlands, Belgium and parts of France and Germany, lighter over Ireland, Scotland and western France" width="48%">
</p>

*Left: Where lions have been recorded in the wild (plain OSM, zoom 3, GBIF).*<br>
*Right: Red fox records since 2000, 900 km around southern England (plain OSM, zoom 7, GBIF).*

<p align="center">
  <img src="sample_pearl_river_delta_1975.png" alt="A disc 150 km around the Pearl River Delta in 1975 with red built-up areas: Guangzhou and Hong Kong, and a scatter of small towns between them, each named" width="48%">
  <img src="sample_pearl_river_delta_2025.png" alt="The same disc in 2025: one continuous red built-up area from Guangzhou through Foshan, Dongguan and Shenzhen to Hong Kong, named Guangzhou, with Zhuhai and Macau and dozens of smaller named towns around it" width="48%">
</p>

*The Pearl River Delta in 1975 and 2025: urban centres of at least 50,000 people, the largest named (plain OSM, 150 km radius, GHSL).*

<p align="center">
  <img src="sample_world_life_expectancy.png" alt="Globe centred on Africa with each country shaded by life expectancy at birth, from pale yellow under 66 years across most of sub-Saharan Africa to dark blue over 82 years in Western Europe, with Somaliland and Western Sahara hatched for no figures" width="48%">
  <img src="sample_europe_unemployment.png" alt="A disc 2,200 km around Germany with each NUTS 2 region shaded by unemployment: darkest over southern Spain, southern Italy, Greece, Finland and Sweden, palest over Czechia, Poland, southern Germany and the Netherlands" width="48%">
</p>

*Left: Life expectancy at birth by country, latest figures (plain OSM, zoom 3, World Bank).*<br>
*Right: Unemployment by NUTS 2 region, latest figures (plain OSM, 2,200 km radius, Eurostat).*

<p align="center">
  <img src="sample_world_petrochemical.png" alt="Globe centred on the Middle East with purple, pink and lilac circles for oil refineries, steam crackers and chemical plants, sized by emissions: thickest around the Gulf, along India's west coast and on the Chinese coast, with Jamnagar, Ulsan and Zhejiang Rongsheng among those named" width="48%">
  <img src="sample_rhine_ruhr_heavy_industry.png" alt="A disc 300 km around the Low Countries with circles for refineries, crackers, chemical plants, steelworks, aluminium and cement works: clusters at Rotterdam, Antwerp, IJmuiden, Ghent, Dunkirk and along the Rhine and Ruhr, the steelworks of Duisburg and IJmuiden largest" width="48%">
</p>

*Left: Oil refineries, steam crackers and chemical plants, sized by emissions (plain OSM, zoom 3, Climate TRACE).*<br>
*Right: Heavy industry from Rotterdam to the Ruhr (plain OSM, 300 km radius, Climate TRACE).*

<p align="center">
  <img src="sample_world_shipping.png" alt="Globe centred on the Arabian Sea with the sea coloured by commercial ship traffic, blue for occasional to orange for the busiest: lanes from Suez and the Gulf past Sri Lanka to the Malacca Strait, fans of lanes across the Indian Ocean to the Cape and Australia, and the Mediterranean, Black Sea and China seas thick with ships" width="48%">
  <img src="sample_channel_shipping.png" alt="A disc 600 km around the Strait of Dover with commercial ship traffic: orange lanes of the traffic separation schemes through the Channel and up the North Sea to Rotterdam and the German Bight, ferries across the Irish Sea, and lighter traffic between" width="48%">
</p>

*Left: Commercial ship traffic from Suez to the Malacca Strait (plain OSM, zoom 3, World Bank and IMF).*<br>
*Right: The lanes of the Channel and the North Sea (plain OSM, 600 km radius, World Bank and IMF).*

<p align="center">
  <img src="sample_world_air_routes.png" alt="Globe centred on the Mediterranean with the airline network as thin magenta great-circle arcs, wider the more airlines fly them: a dense mesh over Europe, a broad band across the North Atlantic to New York and Toronto, and hubs at Moscow, Istanbul, Dubai and Doha, the busiest airports named" width="48%">
  <img src="sample_heathrow_air_routes.png" alt="Globe centred on the North Atlantic with every route out of London Heathrow as a great circle: over Greenland and Canada to North America, south over Africa, and north-east over Scandinavia and Siberia to Asia, the destinations named" width="48%">
</p>

*Left: The airline network, wider for routes more airlines fly (plain OSM, zoom 3, OpenFlights 2014).*<br>
*Right: Every route out of London Heathrow (plain OSM, zoom 3, OpenFlights 2014).*

<p align="center">
  <img src="sample_europe_night.png" alt="Globe centred on the Balkans at night, NASA's Black Marble: Europe's cities as a web of lights from the Low Countries and the Ruhr to Italy and Moscow, the Nile valley a bright thread across the dark desert, the Gulf and India lit up, the seas and the Sahara black" width="48%">
  <img src="sample_romania_night.png" alt="A disc 500 km around Romania at night, its cities filled in red and named with their diacritics, from Timișoara and Cluj-Napoca to Iași, Brașov, Bucharest, Galați and Constanța, and the ports of the Danube and the Black Sea as navy dots sized by the ships calling, Constanța the largest" width="48%">
</p>

*Left: Europe at night (NASA Black Marble 2016, zoom 4).*<br>
*Right: Romania's cities and ports at night (Black Marble, 500 km radius, GHSL cities, IMF PortWatch).*

<p align="center">
  <img src="sample_east_asia_ports.png" alt="Globe centred on the South China Sea with the container ports as navy dots sized by the container ships calling a year, Shanghai, Singapore, Busan and Hong Kong the largest, over the commercial shipping lanes from the Strait of Malacca to Japan and across the Pacific" width="48%">
</p>

*The container ports of East Asia over the shipping lanes (plain OSM, zoom 3, IMF PortWatch, World Bank and IMF).*

<p align="center">
  <img src="sample_ndvi_seasons.png" alt="Two globes centred on the eastern Mediterranean showing vegetation greenness: in January 2026 northern Europe and Russia are bare or snow-covered while southern Africa is green; in July 2026 Europe and Russia are deep green and the Sahel has greened with the rains" width="97%">
</p>

*Vegetation greenness (MODIS NDVI) in January and July 2026: the northern winter and summer, and the southern summer and winter (OSM, zoom 3).*

<p align="center">
  <img src="sample_north_america_landcover.png" alt="Globe centred on North America showing only forests and croplands from MODIS land cover 2024: conifer forests along the Pacific coast and the Rockies, croplands across the Prairies and the Corn Belt, broadleaf and mixed forests in the east, and the Amazon rainforest at the southern edge" width="48%">
  <img src="sample_alps_radius.png" alt="A disc 2,500 km around the Alps, from Scandinavia to the Sahara and from Ireland to Turkey, with the land coloured by height and hill-shaded: the Alps, Pyrenees, Carpathians, Atlas and Anatolian plateau stand out, with distance rings every 500 km" width="48%">
</p>

*Left: MODIS land cover 2024, forests and croplands only, centred on North America (OSM, zoom 3). Much of Canada's boreal forest counts as woody savanna in MODIS, so it is left out.*<br>
*Right: Zoomed in to 2,500 km around the Alps, with elevation (OSM and terrain tiles at zoom 6).*

<p align="center">
  <img src="sample_wind_monsoon.png" alt="Two globes centred on India showing the prevailing winds: in January dry north-east winds blow off Asia across India and the Arabian Sea; in July the south-west monsoon sweeps from the Somali coast across the Arabian Sea onto India, steady and strong, while the south-east trades and the westerlies of the Southern Ocean blow in both" width="97%">
</p>

*The prevailing winds in January and July, 1991–2020 means: the north-east winter monsoon off Asia, and the south-west summer monsoon onto India, with the Somali jet off East Africa (OSM, zoom 3, NCEP/NCAR Reanalysis).*

<p align="center">
  <img src="sample_temperature_seasons.png" alt="Two globes centred on Central Asia showing the mean air temperature: in January Siberia is below -40 °C around Yakutsk and most of Russia and Kazakhstan below -10 °C, while India and Africa stay warm; in July the Sahara, Arabia, Iran and the Indus plain pass 30 °C, Russia is mild and the Tibetan Plateau stays cool" width="97%">
</p>

*Mean air temperature in January and July, 1981–2010: the Siberian winter and the summer heat from the Sahara to the Indus, with Tibet cold in both (OSM, zoom 3, CHELSA v2.1).*

<p align="center">
  <img src="sample_precipitation_monsoon_asia.png" alt="Globe centred on Southeast Asia showing annual precipitation: over 3,000 mm along the Himalayan front, the Burmese and Malabar coasts, Borneo, Sumatra and New Guinea, and under 250 mm across the Taklamakan, the Gobi, Arabia and central Australia" width="48%">
  <img src="sample_humidity_july.png" alt="Globe centred on the Sahara showing relative humidity in July: below 30 % over the central Sahara and Arabia, 50–70 % over Europe, and 70–90 % in the Congo Basin and along the Gulf of Guinea" width="48%">
</p>

*Left: Annual precipitation over monsoon Asia, 1981–2010: the wet Himalayan front, Borneo and New Guinea, and the dry interior of Asia and Australia (OSM, zoom 3, CHELSA v2.1).*<br>
*Right: Relative humidity in July, 1981–2010: the dry Sahara and Arabia between humid Europe and the Gulf of Guinea (OSM, zoom 3, CHELSA v2.1).*

## Features

- Interactive city selection from a built-in list of major metropolitan areas
- Custom latitude/longitude input for arbitrary locations
- Orthographic globe projection centered on the chosen location
- Support for multiple tile providers, including free NASA Blue Marble satellite imagery with no API key
- High-resolution PNG export with transparent background
- Buffered tile fetching to reduce missing imagery near the edge of the globe
- Non-interactive CLI mode with `argparse` for scripting and automation, its options grouped by topic in `--help`
- A local web app (FastAPI): the same maps from a form in the browser, with the matching command line for each map
- Recipe files (`--config`): a map's settings in a small TOML file, one per sample map in [recipes/](recipes/)
- Atlases (`atlas.py`): many maps bound into one PDF with a cover, contents, section pages, captions and questions, edited as a TOML file or in the web app; a Romanian school atlas (Romania, the EU, the world) is bundled
- City marker and label overlay on the globe for named locations
- Concentric geodesic distance circles (2,500 km and 5,000 km) drawn around the centre point with labelled radii, or around another place (`--circles-from`), or none (`--no-circles`)
- Optional zoomed-in map: only the area within a chosen distance of the centre (2,500 km by default), as a disc, with finer tiles
- Optional second globe centred on the antipode, so one image shows the whole Earth, or on any other place, to compare two side by side
- Optional Köppen-Geiger climate classification overlay with compact legend, for all classes or only chosen ones
- Optional Trewartha climate classification overlay, computed from CHELSA v2.1 monthly climate and terrain heights (with a highland group), for all classes or only chosen ones
- Optional elevation layer: land coloured by height and hill-shaded, from open terrain tiles, with a key
- Optional vegetation layer: MODIS land cover (17 IGBP classes, any year 2001–2024, all or chosen classes) or monthly NDVI greenness
- Optional biome layer: the 14 biomes of RESOLVE Ecoregions 2017, its 846 ecoregions with a key naming the largest on the map, or biogeographic realms (WWF's eight, or the 11 zoogeographic realms and 20 regions of Holt et al.'s update of Wallace's map), for all or only chosen biomes
- Optional ocean layers: the sea floor coloured by depth, and the coast of the last ice age, when the sea stood 120 m lower
- Optional plate tectonics: plate boundaries by kind, earthquakes since 1900 by depth and magnitude, and volcanoes
- Optional ocean surface currents, warm and cold, from drifting buoys
- Optional rivers, and river basins coloured and named, inland drainage in grey
- Optional tropical cyclone tracks by Saffir-Simpson category, time zones, and Northern Hemisphere permafrost
- Optional population density (2000–2020) and livestock density (cattle, sheep, goats, pigs, chickens and more)
- Optional malaria parasite rate, *P. falciparum* or *P. vivax*, for any year from 2000 to 2025, and where the dengue mosquitoes *Aedes aegypti* and *Ae. albopictus* have been recorded
- Optional records of any species, genus or family from GBIF, by scientific or common name, for all years or a span of years
- Optional IUCN Red List range of a species, genus or family, from range files you download from IUCN: where it lives, where it may still live, where it was introduced and where it has died out
- Optional big cities: built-up urban centres (metropolitan areas) as their real shape, the largest named, for any five-year step from 1975 to 2030
- Optional statistics: each country shaded by a World Bank figure (GDP, life expectancy, CO₂ per person, internet use and 1,400 more), or each European region by a Eurostat one (unemployment, GDP, population density by NUTS 2 or 3 region), for any year
- Optional industry: refineries, petrochemical and chemical plants, steelworks, aluminium, cement, glass, paper, mines and fossil-fuel power stations worldwide, as circles sized by their emissions, the largest named
- Optional shipping lanes: the sea coloured by how busy it is with commercial ships, fishing boats, ferries, oil and gas vessels or leisure craft, from their AIS positions in 2015–2021
- Optional ports: 2,065 seaports as dots sized by the ships calling a year (2019–2025), of every kind or only container ships, tankers, bulk carriers, general cargo or ro-ro ships, the busiest named
- Night lights: NASA's Black Marble, the Earth at night, as a base map
- Optional air routes: the airline network, or the routes from chosen airports or of chosen airlines, as great-circle arcs wider for routes more airlines fly
- Optional historical borders for any year from 3400 BCE to 2024 (Cliopatria), or any day from 1886 to 2019 with every colony in its ruler's colour (CShapes 2.0): states filled, outlined and named, with a key
- Optional soil overlay: the most probable of 30 World Reference Base soil groups (SoilGrids 2.0), for all groups or only chosen ones
- Optional soil property overlay at a chosen depth: pH, organic carbon, clay, sand, silt, nitrogen and more (SoilGrids 2.0)
- Optional prevailing winds for the year, a season or a month: arrows of the 1991–2020 mean surface wind, coloured by how steady it is
- Optional climate means for the year, a season or a month: air temperature, precipitation or relative humidity over 1981–2010 (CHELSA v2.1), in coloured bands with a key
- Optional temperatures over the sea: the sea surface's (NOAA OISST, 1991–2020), or the air's, carrying the land's air temperature out over the sea
- Optional polar ice: sea ice at its latest winter maximum (NSIDC) and permanent polar land ice
- Optional crop areas for any of 173 crops (CROPGRIDS), one or several at once, with a key
- Any compass direction at the top of the globe (south up, or the direction toward a place), with text kept upright
- Optional route overlays loaded from GeoJSON files, drawn as great-circle polylines and translucent filled areas
- Optional route key below the globe, with short labels and grouping set in the GeoJSON
- Bundled historical route sets: the Portuguese voyages of discovery and the Viking Age
- Graceful error handling for network tile fetch failures
- Automatic attribution block crediting the tile provider and any datasets used

## Supported Cities

The script currently includes:

- NYC
- Moscow
- Shanghai
- London
- Paris
- Berlin
- Ankara
- New Delhi
- Tokyo
- Jakarta
- Manila
- Sao Paulo
- Lagos
- Johannesburg
- Lusaka
- Sydney
- Lisbon
- Oslo
- Honolulu
- Papeete
- San Francisco

## Supported Tile Providers

The interactive menu exposes these providers:

- `osm`: OpenStreetMap
- `osm_plain`: OpenStreetMap with forests, meadows, heath, farmland and sand deserts drawn as plain land, so the layers on top aren't tinted by OSM's land cover colours
- `google`: Google road map (API key required)
- `google_satellite`: Google satellite imagery (API key required; not available to EEA-billed projects, see below)
- `nasa`: NASA Blue Marble satellite imagery with shaded relief and ocean-floor bathymetry; free, public domain, no key
- `nasa_night`: NASA Black Marble, the Earth at night (city lights of 2016); free, public domain, no key

`nasa` is the free satellite option. It comes from NASA's Global Imagery Browse
Services (GIBS): the Blue Marble: Next Generation true-colour land composite
(2004, 500 m per pixel) over shaded relief and bathymetry, in Web Mercator up to
zoom 8, far more than a globe needs. It needs no account or key, has no regional
limits, and is cached like OSM (for a year, since the imagery never changes).
Like Google satellite, it has no place names baked in, so it suits turned globes.

`nasa_night` is NASA's Black Marble, also from GIBS: the night-time lights the
VIIRS instrument on the Suomi NPP satellite saw on cloud-free, moonless nights
of 2016 (~500 m), over a dark blue land and a black sea, up to zoom 8. Cities,
roads, gas flares and fishing fleets show as lights; it is free, public domain,
cached for a year in `~/.cache/ortho_tiles/nasa_night/`, and goes well with
`--cities`, `--ports` and `--industry`. Where a tile cannot be downloaded the
globe stays dark rather than turning pale blue.

`osm_plain` is the standard OSM map with forests, woods, meadows, grassland,
heath, farmland and sand deserts drawn as plain land. OSM shades these green,
olive or yellow; on a radius map most land is covered by one or another, and at
lower zooms their tiny patches blur into a tint over whole countries (farmland
over France, for one), which muddies the climate, crop and soil colours drawn on
top. The tiles are recoloured as they are read: OSM fills each with one colour
(forest `#add19e`, meadow `#cdebb0`, heath `#d6d99f`, farmland `#eef0d5`, sand
`#f5e9c6`), and their anti-aliased edges are mixes of a fill and land, so both
become land colour. Parks, golf courses, orchards, vineyards, scrub, beaches,
water, built-up areas, roads and labels keep their colours; only allotments,
which sit on the same forest-to-land colour line, are lightened too. Forest
names and the green protected-area borders stay. Rocky deserts (much of the
Sahara, the Gobi, the Atacama) are already plain land in OSM. It shares the
`osm` tile cache.

## Requirements

- Python 3.12 or newer (developed on 3.14)
- Internet access for downloading map tiles (and, once each, the data of each layer used)

`requirements.txt` lists minimum versions, not pins. The versions this was developed and tested with:

- `cartopy==0.25.0`
- `matplotlib==3.10.8`
- `numpy==2.4.3`
- `pyproj==3.7.2`
- `shapely==2.1.2`
- `scipy==1.17.1`
- `pillow==12.1.1`
- `python-dotenv==1.2.4`
- `h5py==3.16.0`

## Setup

Install the dependencies:

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

Or install as an editable package (includes a console `ortho` command):

```powershell
pip install -e ".[dev]"
```

For the web app (see "Web App"), add the `web` extra, which brings FastAPI and
uvicorn and the `ortho-web` command:

```powershell
pip install -e ".[web,dev]"
```

## Usage

### Interactive Mode

Run the script with no arguments to enter interactive mode:

```powershell
python ortho.py
```

You will be prompted to choose:

1. A location (pre-defined city **or** custom coordinates)
2. A tile provider
3. Whether to zoom in to a radius around the centre, in km (`y` for 2,500; blank for the whole hemisphere; see "Radius Maps")
4. A zoom level (for a radius map, the default suits the radius)
5. Whether to also draw the opposite hemisphere (whole-globe maps only; see "Both Hemispheres")
6. The compass bearing to put at the top (blank keeps north up; see "Turning the Globe")
7. An optional climate overlay: Köppen-Geiger or Trewartha and the classes to show (blank for all), or the mean temperature, precipitation or humidity and its period (see "Climate Means"); then its opacity/alpha (0–1)
8. Whether to colour the land by height, with relief shading (see "Elevation")
9. An optional vegetation layer: MODIS land cover and the classes to show, or NDVI for a month (see "Vegetation")
10. An optional biome layer: biomes, ecoregions, WWF's realms or Holt's zoogeographic realms, and which biomes (blank for all); then whether to outline Holt's 20 regions (see "Biomes, Ecoregions and Realms")
11. Whether to draw the prevailing winds, and for the year, a season or a month (see "Prevailing Winds")
12. An optional soil overlay: soil groups and which ones (blank for all), or a soil property such as pH (see "Soil Groups" and "Soil Properties")
13. Historical borders: a year (`1500`, `500 BC`) or a date (`1914-08-01`), blank for none (see "Historical Borders")
14. More layers, comma-separated: `ocean-depth`, `ice-age`, `currents`, `plates`, `earthquakes`, `volcanoes`, `rivers`, `basins`, `cyclones`, `time-zones`, `permafrost`, `population`, `malaria`, `aedes`, `livestock:cattle`, `malaria:pv`, `aedes:albopictus`, `species:red fox`, `iucn:lion`, `cities`, `cities:1975`, `indicator:life-expectancy`, `indicator:eurostat:unemployment`, `industry:petrochemical`, `shipping`, `shipping:fishing`, `ports`, `ports:container`, `air-routes`, `air-routes:LHR+JFK` … (blank for none)
15. Whether to add polar ice at its winter maximum (see "Polar Ice")
16. Crops to shade, comma-separated, e.g. `wheat, rice` (blank to skip, `?` to list them; see "Crop Areas")
17. An optional GeoJSON route file to overlay (leave blank to skip)
18. Whether to add a key naming each route (asked only when a route file is loaded)

### CLI Mode

Pass arguments directly for non-interactive use:

```powershell
# Pre-defined city
python ortho.py --city NYC --provider google --zoom 3 --dpi 600

# Custom coordinates (Tokyo)
python ortho.py --lat 35.6762 --lon 139.6503 --provider osm --zoom 2

# Explicit output path
python ortho.py --city paris --provider osm --zoom 3 -o my_globe.png

# Save to a specific directory
python ortho.py --city london --provider google_satellite --zoom 2 --output-dir renders/

# Draw a route from a GeoJSON file (repeat --route for more)
python ortho.py --city lisbon --zoom 3 --route routes/gibraltar_ascension_falklands.geojson

# A route file with a key naming each route (see "Route Overlays")
python ortho.py --city lisbon --provider google_satellite --route routes/portuguese_explorers.geojson --route-legend

# The whole Earth: Shanghai's globe and its antipode side by side (see "Both Hemispheres")
python ortho.py --city shanghai --both-hemispheres

# Two places side by side: Lisbon's globe and Tokyo's (see "Two Places Side by Side")
python ortho.py --city lisbon --beside tokyo

# Polar ice at its latest winter maximum, or at a chosen year's (see "Polar Ice")
python ortho.py --city moscow --ice
python ortho.py --lat 90 --lon 0 --ice-year 2012

# Where wheat is grown; several crops show the largest one in each place (see "Crop Areas")
python ortho.py --lat 45 --lon 40 --crop wheat
python ortho.py --lat 30 --lon 95 --crop wheat --crop rice --crop maize

# Only some climate classes: oceanic climates, or every Mediterranean one (see "Köppen-Geiger Climate Overlay")
python ortho.py --city london --koppen-class Cfb --koppen-alpha 0.7
python ortho.py --lat 30 --lon 0 --koppen-class Cs --both-hemispheres

# The Trewartha classification instead, whole or one group (see "Trewartha Climate Overlay")
python ortho.py --city london --trewartha
python ortho.py --city lusaka --trewartha-class C --climate-alpha 0.7

# Land coloured by height, with hill shading (see "Elevation")
python ortho.py --lat -15 --lon -65 --elevation

# Land cover, all classes or some, and vegetation greenness in winter and summer (see "Vegetation")
python ortho.py --lat 10 --lon 20 --landcover
python ortho.py --lat 50 --lon -95 --landcover-class forest --landcover-class cropland
python ortho.py --lat 35 --lon 30 --ndvi january
python ortho.py --lat 35 --lon 30 --ndvi 2026-07

# The sea floor by depth, and the last ice age's coast (see "Ocean Depth and the Ice Age Coast")
python ortho.py --lat 55 --lon 3 --radius 2500 --ocean-depth --ice-age

# Plate boundaries, earthquakes since 1900 and volcanoes (see "Plate Tectonics")
python ortho.py --lat 10 --lon 150 --plates --earthquakes --volcanoes

# Ocean currents with hurricane tracks; rivers and basins (see "Ocean Currents", "Rivers and Basins")
python ortho.py --lat 25 --lon -50 --currents --cyclones
python ortho.py --lat 0 --lon 20 --rivers --basins

# Permafrost and time zones; population; livestock (see the sections of the same names)
python ortho.py --lat 75 --lon 90 --permafrost --time-zones
python ortho.py --lat 25 --lon 90 --population 2020
python ortho.py --lat -30 --lon 145 --livestock sheep

# Malaria in 2025 and in 2000; the dengue mosquitoes (see "Malaria and Aedes Mosquitoes")
python ortho.py --lat 5 --lon 20 --malaria pf
python ortho.py --lat 15 --lon 80 --malaria pv --malaria-year 2000
python ortho.py --lat 0 --lon -70 --aedes

# Where a species has been recorded (see "Species Records")
python ortho.py --lat 0 --lon 20 --species lion
python ortho.py --lat 52 --lon 0 --radius 900 --species "red fox" --species-years 2000-2025

# A species' IUCN range, from range files you downloaded (see "Species Ranges")
python ortho.py --lat 10 --lon 30 --iucn lion --species lion

# Big cities, and how they grew (see "Cities")
python ortho.py --lat 25 --lon 100 --cities
python ortho.py --config recipes/pearl_river_delta_1975.toml

# Statistics by country or European region (see "Statistics")
python ortho.py --lat 10 --lon 20 --indicator life-expectancy
python ortho.py --lat 50 --lon 12 --radius 2200 --indicator eurostat:unemployment

# Industry: refineries, crackers, steelworks … sized by emissions (see "Industry")
python ortho.py --lat 30 --lon 60 --industry petrochemical
python ortho.py --lat 51.3 --lon 5.5 --radius 300 --industry heavy

# Shipping lanes and air routes (see "Shipping, Ports and Air Routes")
python ortho.py --lat 20 --lon 70 --shipping
python ortho.py --lat 51.5 --lon 2 --radius 600 --shipping
python ortho.py --lat 40 --lon 10 --air-routes all
python ortho.py --lat 45 --lon -20 --air-routes LHR

# The Earth at night, and ports (see "Supported Tile Providers" and "Shipping, Ports and Air Routes")
python ortho.py --lat 45 --lon 18 --provider nasa_night --zoom 4
python ortho.py --lat 45.4 --lon 26.5 --radius 500 --provider nasa_night --cities --ports
python ortho.py --lat 25 --lon 118 --ports container --shipping

# The states of a year or a day: 1500, the Roman world in 117, Africa on 1 July 1914 (see "Historical Borders")
python ortho.py --city Lisbon --borders 1500
python ortho.py --lat 40 --lon 20 --borders 117 --radius 3000
python ortho.py --lat 5 --lon 20 --borders 1914-07-01

# Biomes, all or some, the ecoregions within 1,200 km of Madagascar, or the realms (see "Biomes, Ecoregions and Realms")
python ortho.py --lat 5 --lon 20 --biomes
python ortho.py --lat 10 --lon 100 --biome-class mangroves --biome-class "tropical moist"
python ortho.py --lat -19 --lon 46.5 --radius 1200 --ecoregions
python ortho.py --city Shanghai --realms
python ortho.py --city Shanghai --realms holt --realm-regions

# Soil groups, all or some: the steppe's black earths (see "Soil Groups")
python ortho.py --lat 35 --lon 40 --soil
python ortho.py --lat 50 --lon 60 --soil-class Chernozems --soil-class Kastanozems

# A soil property at a depth: topsoil pH, or organic carbon at 15-30 cm (see "Soil Properties")
python ortho.py --lat 25 --lon 20 --soil-property ph
python ortho.py --lat 60 --lon -100 --soil-property organic-carbon --soil-depth 15-30cm

# Zoomed in: 2,500 km around the centre, or any radius (see "Radius Maps")
python ortho.py --lat 46 --lon 10 --radius --elevation
python ortho.py --city tokyo --radius 800

# Prevailing winds over the year, a season or a month (see "Prevailing Winds")
python ortho.py --lat 15 --lon -30 --wind
python ortho.py --lat 15 --lon 75 --wind jja
python ortho.py --lat 15 --lon 75 --wind january

# Mean temperature, precipitation or humidity over the year, a season or a month (see "Climate Means")
python ortho.py --lat 45 --lon 70 --temperature january
python ortho.py --lat 5 --lon 100 --precipitation
python ortho.py --lat 30 --lon 20 --humidity jja
python ortho.py --lat 46 --lon 10 --radius --temperature july --elevation

# The sea too: its surface temperature, or the air over it with the land's (see "Temperatures over the Sea")
python ortho.py --lat 20 --lon -40 --sea-temperature july
python ortho.py --lat 20 --lon -40 --temperature july --temperature-over-sea

# South up, or the direction toward a place at the top (see "Turning the Globe")
python ortho.py --city sydney --up 180
python ortho.py --city lisbon --provider google_satellite --up-toward "New Delhi"
```

#### CLI Flags

| Flag | Description | Default |
|---|---|---|
| `--city CITY` | Pre-defined city (case-insensitive) | — |
| `--lat LAT` | Custom latitude (-90 to 90) | — |
| `--lon LON` | Custom longitude (-180 to 180) | — |
| `--provider` | Tile provider: `osm`, `osm_plain`, `google`, `google_satellite`, `nasa`, `nasa_night` (the Earth at night) | `osm` |
| `--zoom ZOOM` | Tile zoom level: 1–4 for the whole globe; with `--radius`, up to one above its default | `3`, or chosen from `--radius` and the latitude |
| `--dpi DPI` | Output resolution, 10–1200. The figure is 20 in, so 300 DPI = 6,000 px | `300` |
| `-o`, `--output` | Explicit output filepath (overrides auto-naming) | — |
| `--output-dir` | Directory for auto-named output files | `.` |
| `--cache-dir` | OSM and NASA tile cache directory (Google tiles are never cached) | `~/.cache/ortho_tiles` |
| `--no-cache` | Download map, terrain (`--elevation`) and vegetation tiles fresh, without reading or filling the cache | off |
| `--koppen` | Enable Köppen-Geiger climate classification overlay | off |
| `--koppen-class CLASS` | Show only this climate class (`Cfb`) or group (`C`, `Cs`); repeat for several; implies `--koppen` | all |
| `--trewartha` | Enable the Trewartha climate classification overlay (not with `--koppen`) | off |
| `--trewartha-class CLASS` | Show only this Trewartha class (`Do`) or group (`C`); repeat for several; implies `--trewartha` | all |
| `--temperature [PERIOD]` | Colour the land by its 1981–2010 mean air temperature for `year`, a season (`djf`, `mam`, `jja`, `son`) or a month (`july`, `7`) | off (`year` when given alone) |
| `--precipitation [PERIOD]` | Colour the land by its 1981–2010 precipitation total over PERIOD, as for `--temperature` | off (`year` when given alone) |
| `--humidity [PERIOD]` | Colour the land by its 1981–2010 mean relative humidity over PERIOD, as for `--temperature` | off (`year` when given alone) |
| `--temperature-over-sea` | With `--temperature`, colour the sea and lakes by the air temperature over them too (NCEP/NCAR, 1981–2010, ~1.9°) | off |
| `--sea-temperature [PERIOD]` | Colour the sea and lakes by their 1991–2020 mean surface temperature (NOAA OISST v2.1, 0.25°), alongside any climate layer but `--temperature-over-sea` | off (`year` when given alone) |
| `--climate-alpha ALPHA` | Opacity of the climate layer (0–1); `--koppen-alpha` is the same option | `0.45` for a classification, `0.7` for a climate mean |
| `--elevation` | Colour the land by height and shade its relief, with a key | off |
| `--elevation-alpha ALPHA` | Opacity of the elevation layer (0–1) | `0.8` |
| `--landcover` | Colour the land by its MODIS land cover class, with a key | off |
| `--landcover-class CLASS` | Show only this class or group (`forest`, `shrubland`, `savanna`, `grassland`, `wetland`, `cropland`, `urban`, `ice`, `barren`, or the start of a class name such as `evergreen`); repeat for several; implies `--landcover` | all |
| `--landcover-year YEAR` | Year of the land cover, 2001–2024; implies `--landcover` | `2024` |
| `--ndvi MONTH` | Vegetation greenness for a month: `YYYY-MM`, or a month name or number for its latest year (not with `--landcover`) | — |
| `--vegetation-alpha ALPHA` | Opacity of the land cover or NDVI layer (0–1) | `0.7` |
| `--biomes` | Colour the land by its biome (RESOLVE Ecoregions 2017), with a key | off |
| `--biome-class BIOME` | Show only this biome, by number (`1`–`14`) or words from its name (`mangroves`, `temperate`, `forest`, `rock`); repeat for several; implies `--biomes` | all |
| `--ecoregions` | Colour each of the 846 ecoregions instead of their biomes (with `--biome-class`, only those biomes' ecoregions); the key names the largest on the map | off |
| `--realms [SOURCE]` | Colour biogeographic realms instead of the biomes: `wwf` for WWF's eight (with `--biome-class`, only in those biomes), or `holt` for Holt et al.'s 11 zoogeographic realms (not with `--biomes` or `--biome-class`); not with `--ecoregions` | off (`wwf` when given alone) |
| `--realm-regions` | Outline Holt et al.'s 20 zoogeographic regions and name those on the map, over any layer | off |
| `--biome-alpha ALPHA` | Opacity of the biome, ecoregion or realm layer (0–1) | `0.6` |
| `--soil` | Colour the land by its most probable soil group, with a key | off |
| `--soil-class GROUP` | Show only this soil group, by name (`Chernozems`) or WRB code (`CH`); repeat for several; implies `--soil` | all |
| `--soil-alpha ALPHA` | Opacity of the soil overlay, groups or property (0–1) | `0.6` |
| `--soil-property PROPERTY` | Draw a soil property instead of the groups: `ph`, `organic-carbon`, `clay`, `sand`, `silt`, `nitrogen`, `cec`, `bulk-density`, `coarse-fragments`, `carbon-stock` | — |
| `--soil-depth DEPTH` | Depth of `--soil-property`: `0-5cm`, `5-15cm`, `15-30cm`, `30-60cm`, `60-100cm`, `100-200cm` (`carbon-stock`: `0-30cm`) | `0-5cm` |
| `--wind [PERIOD]` | Draw the prevailing winds (1991–2020 mean 10 m wind) for `year`, a season (`djf`, `mam`, `jja`, `son`) or a month (`july`, `7`) | off (`year` when given alone) |
| `--ice` | Draw sea ice at its winter maximum and polar land ice | off |
| `--ice-year YEAR` | Year of the sea ice maxima, 1979 on; implies `--ice` | latest published |
| `--borders WHEN` | Draw the states of a year (`1500`, `-500`, `500 BC`) or, from 1886, a day (`1914-08-01`), filled, outlined and named, with a key | — |
| `--borders-source SOURCE` | `cliopatria` (3400 BCE–2024), `cshapes` (1886–2019, with colonies) or `auto` (CShapes in its years, Cliopatria otherwise) | `auto` |
| `--borders-alpha ALPHA` | Opacity of the borders' fill (0–1) | `0.55` |
| `--ocean-depth` | Colour the sea floor by depth and shade it, with a key (opacity: `--elevation-alpha`) | off |
| `--ice-age` | Draw the last ice age: the land it left dry (sea ~120 m lower), its coast, and its ice sheets | off |
| `--plates` | Draw the plate boundaries by kind and name the large plates | off |
| `--earthquakes [MAGNITUDE]` | Draw the earthquakes since 1900 of at least MAGNITUDE (5–9), sized by magnitude, coloured by depth | off (`6` when given alone) |
| `--volcanoes` | Draw the Holocene volcanoes, red if erupted since 1900 | off |
| `--currents` | Draw the mean ocean surface currents, coloured warm or cold for their latitude | off |
| `--rivers` | Draw the main rivers and name the greatest | off |
| `--basins` | Colour the river basins of 100,000 km² or more, named, inland drainage in grey | off |
| `--basins-alpha ALPHA` | Opacity of the river basins (0–1) | `0.5` |
| `--cyclones [SINCE]` | Draw the tropical cyclone tracks from the year SINCE (1980 on), by Saffir-Simpson category | off (`1980` when given alone) |
| `--time-zones` | Shade and label the standard-time zones | off |
| `--permafrost` | Colour the Northern Hemisphere's permafrost zones | off |
| `--permafrost-alpha ALPHA` | Opacity of the permafrost (0–1) | `0.7` |
| `--population [YEAR]` | Colour the land by population density in 2000, 2005, 2010, 2015 or 2020 | off (`2020` when given alone) |
| `--population-alpha ALPHA` | Opacity of the population density (0–1) | `0.7` |
| `--livestock ANIMAL` | Colour the land by the density of `cattle`, `buffaloes`, `horses`, `sheep`, `goats`, `pigs`, `chickens` or `ducks` in 2015 | — |
| `--livestock-alpha ALPHA` | Opacity of the livestock density (0–1) | `0.7` |
| `--malaria PARASITE` | Colour the land by the malaria parasite rate: `pf` (*P. falciparum*, children aged 2–10) or `pv` (*P. vivax*, all ages) | — |
| `--malaria-year YEAR` | Year of the malaria map, 2000–2025 | `2025` |
| `--malaria-alpha ALPHA` | Opacity of the malaria map (0–1) | `0.75` |
| `--aedes [SPECIES]` | Draw where *Aedes* mosquitoes have been recorded, 1958–2014: `both`, `aegypti` or `albopictus` | off (`both` when given alone) |
| `--species NAME` | Draw where a species, genus or family has been recorded in the wild (GBIF), by scientific or common name: `"Panthera leo"`, `"red fox"`, `Quercus` | — |
| `--species-years YEARS` | Only the records of these years: `FROM-TO` or `YEAR` | all years |
| `--iucn NAME` | Draw the IUCN Red List range of a species, genus or family, by scientific or common name: `"Panthera leo"`, `lion`, `Felidae` | — |
| `--iucn-data PATH` | Folder, zip, `.shp` or `.gpkg` of IUCN or BirdLife range files | `~/.cache/ortho_tiles/iucn` |
| `--iucn-alpha ALPHA` | Opacity of the IUCN range (0–1) | `0.45` |
| `--cities [YEAR]` | Draw the built-up urban centres and name the largest, as of `YEAR` (1975–2030 every five years; 2030 projected) | off (`2025` when given alone) |
| `--cities-min PEOPLE` | Show only centres of at least this many people | 1,000,000 on a whole globe, fewer on a closer map (down to 50,000) |
| `--indicator NAME` | Shade each country by a World Bank statistic (`life-expectancy`, `gdp-per-person`, a code such as `NY.GDP.PCAP.CD`) or each European region by a Eurostat one (`eurostat:unemployment`, `eurostat:DATASET?dim=CODE&level=nuts3`) | — |
| `--indicator-year YEAR` | Year of the figures; an area without one that year shows its latest from the nine years before | each area's latest |
| `--indicator-alpha ALPHA` | Opacity of the statistics (0–1) | `0.75` |
| `--industry SECTORS` | Draw industrial plants as circles sized by emissions, the largest named: comma-separated sectors (`refineries`, `petrochemicals`, `steel`, `cement`, `power` …) or groups (`petrochemical`, `heavy`, `mining`, `all`) | — |
| `--shipping [KIND]` | Colour the sea by how busy it is with ships (AIS 2015–2021): `commercial` (default), `all`, `fishing`, `passenger`, `oil-gas` or `leisure` | — |
| `--shipping-alpha ALPHA` | Opacity of the ship traffic (0–1) | `0.85` |
| `--ports [KIND]` | Draw the ports as dots sized by the ships calling a year (2019–2025), the busiest named: `all` (default), `container`, `tanker`, `dry-bulk`, `general-cargo` or `roro` | — |
| `--air-routes AIRPORTS` | Draw air routes as great-circle arcs: `all`, or airports by code or city (`LHR`, `EGLL`, `London`) and airlines (`airline:BA`), comma-separated | — |
| `--crop NAME[:COLOUR]` | Shade where a crop is grown; repeat for several crops | — |
| `--list-crops` | List the 173 crop names `--crop` accepts, then exit | — |
| `--route GEOJSON` | GeoJSON route file to draw; repeat for multiple files | — |
| `--route-legend` | Add a key below the globe naming each route next to its colour | off |
| `--radius [KM]` | Zoom in: show only the area within KM of the centre, 100–10,000 (not with `--both-hemispheres`) | off (`2500` when given alone) |
| `--both-hemispheres` | Draw a second globe centred on the antipode, showing the whole Earth; `--dpi` up to 600 | off |
| `--beside PLACE` | Draw a second globe beside the first, centred on a city or `LAT,LON` (not with `--both-hemispheres`); `--dpi` up to 600 | — |
| `--up BEARING` | Compass bearing to put at the top, in degrees clockwise from north | `0` |
| `--up-toward PLACE` | Put the direction toward a city or `LAT,LON` at the top (instead of `--up`) | — |
| `--circles-from PLACE` | Measure the distance circles from a city or `LAT,LON` instead of the map's centre, marked with a hollow ring | the centre |
| `--no-circles` | Leave out the distance circles | off |
| `--config FILE` | Read settings from a TOML recipe file; the command line overrides it (see "Recipe Files") | — |

> **Note:** `--city` and `--lat` are mutually exclusive. When using `--lat`, `--lon` is required.
> One climate layer at a time: `--koppen`, `--trewartha`, `--temperature`, `--precipitation` and `--humidity` exclude each other. `--sea-temperature` colours the sea alongside any of them.

`python ortho.py --help` lists the options in groups: Location, Imagery and
output, Globe layout, Climate, Elevation, Vegetation, Biomes, ecoregions and realms, Soil,
Historical borders, Plate tectonics, rivers, cyclones, time zones, permafrost, People and livestock,
Malaria and Aedes mosquitoes, Species records (GBIF), Species ranges (IUCN Red List and BirdLife), Cities (GHSL urban centres), Statistics by country or region (World Bank, Eurostat), Industry (Climate TRACE), Shipping, ports and air routes (World Bank and IMF, OpenFlights), Winds, Polar ice, Crops, Routes and areas, Recipes.

### Recipe Files

A recipe is a small TOML file holding a map's settings, so a long command becomes
`python ortho.py --config recipes/viking_routes.toml`. Its keys are the option
names without the dashes (`route-legend` or `route_legend`); a list repeats an
option and `true` turns a flag on:

```toml
# recipes/viking_routes.toml
lat = 62
lon = 15
provider = "osm"
route = [
    "../routes/viking_homelands.geojson",
    "../routes/viking_trade.geojson",
]
route-legend = true
ice = true
output = "orthographic_map_scandinavia_osm_z3_vikings_ice.png"
```

- **The command line wins.** Options typed after `--config` override the recipe:
  `--config recipes/viking_routes.toml --provider nasa --dpi 600`. Repeatable
  options (`--route`, `--crop`, `--koppen-class`, `--trewartha-class`, `--soil-class`,
  `--landcover-class`) add to the recipe's list.
  Giving `--city` or `--lat`/`--lon` replaces the recipe's location, and `--up`
  or `--up-toward` replaces its orientation, so a recipe works anywhere:
  `--config recipes/london_wheat.toml --city lusaka -o lusaka_wheat.png`. (The
  bundled recipes name their output file, so give `-o` when you change the place.)
- **Paths.** Route files in a recipe are relative to the recipe file, so recipes
  run from any folder. Output paths (`output`, `output-dir`) are relative to
  where you run the command, as on the command line.
- **Checks.** Recipe values go through the same checks as typed options (allowed
  providers, zoom range, crop and climate names, …). An unknown key stops with a
  suggestion (`citty` → "Did you mean 'city'?"). `config` and `list-crops` can't
  be set in a recipe.
- A `false` flag simply leaves it off; to turn off a flag a recipe sets, edit the
  recipe.

[recipes/](recipes/) has one recipe per sample map in this README:

| Recipe | Sample |
|---|---|
| `sao_paulo.toml` | São Paulo on Google satellite imagery, with distance rings |
| `london_koppen.toml` | London with the Köppen-Geiger climate overlay |
| `portuguese_voyages.toml` | The Portuguese voyages of discovery, with a key |
| `portuguese_hemispheres.toml` | The Portuguese voyages on both hemispheres |
| `tokyo_lisbon.toml` | Tokyo and Lisbon side by side, with the prevailing winds, polar ice, and wheat or rice |
| `viking_routes.toml` | The Viking Age, with polar ice |
| `zheng_he_voyages.toml` | The voyages of Zheng He's treasure fleets, a radius map of the Indian Ocean with the Köppen-Geiger B, Dw, E and EF (ice cap) climates |
| `london_wheat.toml` | Where wheat is grown, centred on London |
| `shanghai_wheat_rice.toml` | Wheat or rice, centred on Shanghai |
| `nyc_toward_london.toml` | New York turned toward London, NASA imagery |
| `lusaka_cwa.toml` | Zambia's dominant climate, Cwa |
| `himalaya_trewartha.toml` | The Trewartha climate classification across Asia |
| `andes_elevation.toml` | Elevation, centred on South America |
| `steppe_soils.toml` | The steppe soils, centred on Kazakhstan |
| `topsoil_ph.toml` | Topsoil pH across Africa, Europe and western Asia |
| `africa_biomes.toml` | Africa's biomes, on a plain map |
| `madagascar_ecoregions.toml` | Madagascar's ecoregions, 1,200 km around the island |
| `borders_1500_voyages.toml` | The states of 1500 with the Portuguese voyages of discovery |
| `africa_1914.toml` | Africa's borders on 1 July 1914 |
| `ice_age_north_sea.toml` | The North Sea in the last ice age, with the sea floor by depth |
| `ring_of_fire.toml` | Plate boundaries, earthquakes and volcanoes of the western Pacific |
| `atlantic_currents_hurricanes.toml` | The Atlantic's currents and hurricane tracks |
| `africa_river_basins.toml` | Africa's river basins and rivers |
| `asia_population.toml` | Population density in 2020, centred on the Bay of Bengal |
| `india_cattle.toml` | Cattle per km² in 2015, centred on India |
| `north_america_landcover.toml` | MODIS land cover 2024, forests and croplands, centred on North America |
| `ndvi_january.toml`, `ndvi_july.toml` | Vegetation greenness in January and July 2026 (the two halves of the NDVI sample) |
| `alps_radius.toml` | 2,500 km around the Alps, with elevation |
| `wind_january.toml`, `wind_july.toml` | The prevailing winds over India and the Indian Ocean in January and July (the two halves of the monsoon sample) |
| `temperature_january.toml`, `temperature_july.toml` | Mean air temperature in January and July, centred on Central Asia (the two halves of the temperature sample) |
| `precipitation_monsoon_asia.toml` | Annual precipitation over monsoon Asia |
| `humidity_july.toml` | Relative humidity in July, centred on the Sahara |

The Google satellite recipes need a Google Maps key and are affected by Google's
EEA restriction (see "Google tiles" below); add `--provider nasa` to render them
with free satellite imagery instead.

### Output Naming

The script writes a PNG named like:

```text
orthographic_map_<city>_<provider>_z<zoom>.png
```

Example:

```text
orthographic_map_paris_osm_z3.png
```

A radius map adds `_r<km>km`, both hemispheres `_hemispheres` and a turned
globe `_up<bearing>`, e.g. `orthographic_map_tokyo_osm_z8_r800km.png`; a globe
beside the first joins the two places, as in `orthographic_map_lisbon_and_tokyo_osm_z3.png`.

### Web App

`webapp.py` serves the same maps from a form in the browser. It needs the `web`
extra (`pip install -e ".[web]"`, or `pip install fastapi uvicorn`):

```powershell
python webapp.py          # or: ortho-web
```

Then open http://127.0.0.1:8000. Choose a place, the globe and the layers, and
press **Render map**. The map appears when it is drawn, with a PNG download and
the `ortho.py` command line that draws the same map. The page remembers your
last settings in the browser.

- **Recipes.** The Recipe panel at the top loads any recipe from `recipes/` into
  the form, so its layers and routes can be changed, added or removed there.
  **Save** writes the form back as a recipe under the name given (a new name
  makes a new recipe, an existing one asks before it is replaced), with the
  description as its opening comment; **Delete** removes the recipe chosen. A
  saved recipe draws the same map with `--config`. What the form does not set is
  kept from the file as it was (`output`, `dpi`, routes outside `routes/`);
  comments after an option are not. Route paths are written relative to the
  recipe, as the bundled ones are.
- **Routes.** The Routes panel lists the GeoJSON files in `routes/` with the
  routes in each; add any of them to the map, remove them, and turn on the key
  naming each route. **Upload** checks a GeoJSON file of your own (as
  `--route` would, up to 5 MB) and saves it into `routes/` under a tidied name
  (`Silk Road.json` becomes `silk_road.geojson`), so recipes can use it.
- **Atlases.** The **Atlases** link opens the atlas page, `/atlas`, which edits
  the atlases in `atlases/` and builds their PDFs (see "Atlases (PDF)" below).

- **Same checks as the CLI.** A request is checked by the same code as a command
  line (`ortho.prepare_render`), so a bad class name or period is refused with
  the CLI's message before any tiles are fetched. The CLI is unchanged.
- **One map at a time.** Maps are drawn one after another on a worker thread.
  A request waits in a queue (10 at most) and the page shows its place in it.
- **Limits.** Maps default to 150 dpi and may go up to 300. Google tiles are
  offered only when an API key is set (see "Google tiles").
- **Files.** Maps are written to a folder in the system's temporary directory,
  and the latest 50 are kept. The tile and data caches are the CLI's, so data
  downloaded by either is reused by the other.
- **API.** The page uses a small JSON API, also listed at `/docs`:
  `POST /api/maps` takes the CLI's options by their argparse names
  (`{"city": "Lisbon", "temperature": "july"}`) and returns a map id;
  `GET /api/maps/{id}` gives its status; `GET /api/maps/{id}/image` gives the
  PNG (`?download=1` to save it); `GET /api/options` lists the choices.
  `GET /api/recipes` and `GET`, `PUT` and `DELETE /api/recipes/{name}` list,
  read, save and delete recipes; `GET /api/routes` lists the route files and
  `POST /api/routes` adds one. Routes in a map request are named by their file
  in `routes/` (`{"route": ["viking_trade.geojson"], "route_legend": true}`).
- **Settings.** The server listens on `127.0.0.1` only (this computer), so no one
  else can reach it. Change that with `--host` and `--port`, or with these
  environment variables, which also suit a hosted server:

| Variable | Meaning | Default |
|----------|---------|---------|
| `ORTHO_WEB_HOST` | Address to listen on | `127.0.0.1` |
| `ORTHO_WEB_PORT` (or `PORT`) | Port to listen on | `8000` |
| `ORTHO_WEB_OUTPUT_DIR` | Where the maps are written | `<temp>/ortho_web` |
| `ORTHO_WEB_CACHE_DIR` | Tile cache | the CLI's, `~/.cache/ortho_tiles` |
| `ORTHO_WEB_MAX_DPI` | Largest dpi a request may ask for | `300` |
| `ORTHO_WEB_MAX_QUEUE` | Maps that may wait or render at once | `10` |
| `ORTHO_WEB_KEEP` | Finished maps kept | `50` |
| `ORTHO_WEB_RECIPES_DIR` | The recipes the form loads and saves | `recipes/` beside `webapp.py` |
| `ORTHO_WEB_ROUTES_DIR` | The route files the form offers and uploads into | `routes/` beside `webapp.py` |
| `ORTHO_WEB_ATLASES_DIR` | The atlases the atlas page edits and prints | `atlases/` beside `webapp.py` |
| `ORTHO_WEB_READ_ONLY` | `1` to offer recipes, routes and atlases without saving, deleting or uploading any; set it on a server others can reach | off |

### Atlases (PDF)

`atlas.py` binds many maps into one printable PDF: a cover, the contents, a
title page for each part, and a page for each map with its title, a caption
and questions for the reader. An atlas is a TOML file;
[atlases/romania_school.toml](atlases/romania_school.toml) is a school atlas in
Romanian, in three parts: **România** (position, relief, rivers, climate,
temperatures, precipitation, vegetation, soils, crops, population, cities,
development regions, industry, night lights, the Vrancea earthquakes, and the
borders of 1914 and 1938), **Uniunea Europeană** (the states, relief, climates,
January temperatures, precipitation, biomes, population, cities, GDP and
unemployment by region, night lights, shipping, Europe in 1914) and **Lumea**
(the two hemispheres, the states, relief and the ocean floor, plate tectonics,
climates, currents, biomes, population, cities, life expectancy, GDP, time zones,
shipping, the polar regions): 49 pages, 47 maps.

```powershell
python atlas.py atlases/romania_school.toml           # draws the maps and writes atlas_romania_school.pdf
python atlas.py atlases/romania_school.toml --proof   # lays out the pages without drawing any map
python atlas.py atlases/romania_school.toml --check   # checks every map's options and lists the pages
```

An atlas file holds a few settings and then its pages in order:

```toml
title = "Atlas geografic școlar"
subtitle = "România · Uniunea Europeană · Lumea"
language = "ro"     # the labels ("Cuprins", "Întrebări și exerciții" …): "ro" or "en"
paper = "a4"        # a4, a3 or letter
dpi = 120           # the maps' resolution (50–300); a globe is 20 in, so 120 dpi is 2,400 px

[cover.map]         # a map for the cover (optional)
lat = 45.95
lon = 24.95
provider = "nasa"
no-circles = true

[[pages]]
type = "section"    # a part's title page: "section"; a map: "map" (the default with a map); "text"
title = "România"
text = "What this part shows."

[[pages]]
title = "Harta fizică a României"
text = """
The caption below the map. A blank line starts a new paragraph.
"""
questions = ["Numește cele trei ramuri ale Carpaților Românești."]
[pages.map]         # the map's options, exactly as a recipe has them
lat = 45.95
lon = 24.95
provider = "osm_plain"
radius = 450
no-circles = true
elevation = true
rivers = true
```

- **Editing.** Change the text, reorder the `[[pages]]` tables, delete or add
  pages. A map page takes a `[pages.map]` table, a `recipe = "../recipes/x.toml"`
  (relative to the atlas), or both, the table's options on top of the recipe's.
  `orientation` is `auto` (landscape when the map is wide, as two globes are),
  `portrait` or `landscape`. The atlas sets `dpi` and the output for every map,
  so a page's map may not.
- **Checked first.** Every map is checked like a command line before anything is
  drawn, and a mistake names its page: `romania_school.toml page 5 (Clima):
  Unknown Köppen-Geiger class 'Zzz'`.
- **Drawn once.** The maps are kept in `atlas_<name>_maps/`, each named after
  its page and a hash of its options, so a build draws only the maps that are
  new or changed: rewording a caption and building again takes seconds.
  `--force` draws them all again; `--maps-dir` and `-o` choose other places.
  The first build of the bundled atlas downloads every layer's data and takes a
  long while.
- **A map that cannot be drawn** (no network, say) gets a placeholder naming the
  error; the rest of the PDF is written and the command exits with status 1.
- **The web app** edits atlases too: open http://127.0.0.1:8000/atlas to edit
  the settings, titles, text and questions, move, duplicate, add and delete
  pages, and build the PDF (or a quick proof) to open or download. **Edit the
  map in the form** opens a page's map in the map form, to change and render it
  there, then **Save to the atlas**. Saving from the web app keeps the comment
  at the top of the file but not the others. The API: `GET /api/atlases`,
  `GET`/`PUT`/`DELETE /api/atlases/{name}`, `GET`/`PUT /api/atlases/{name}/maps/{page}`
  (`page` is the page's number or `cover`), and `POST /api/atlases/{name}/pdf`
  (`{"proof": true}` for a proof), followed like a map at `/api/maps/{id}`, with
  the PDF at `/api/maps/{id}/pdf`.

## Programmatic Use

You can also import the generator directly:

```python
from ortho import generate_orthographic_map

generate_orthographic_map(
    lat=48.8566,
    lon=2.3522,
    output_filename="orthographic_map_paris_osm_z3.png",
    tile_provider="osm",
    zoom=3,
    dpi=300,
    output_dir="renders",  # optional: save to a specific directory
    city_name="Paris",     # optional: adds a marker and label on the map
    routes=None,           # optional: list of Route objects, see "Route Overlays"
    route_legend=False,    # optional: add a key naming each route
    both_hemispheres=False,  # optional: add a globe centred on the antipode
    beside=None,           # optional: (lat, lon) of a second globe instead, with beside_name="Tokyo" to label it
    radius_km=None,        # optional: e.g. 2500 shows only that far from the centre (raise zoom to suit)
    wind=None,             # optional: prevailing winds for "year", "djf"…"son" or a month such as "july"
    temperature=None,      # optional: mean temperature for a period as for wind; or precipitation=, humidity=
    temperature_over_sea=False,  # optional: with temperature, the air over the sea too
    sea_temperature=None,  # optional: the sea's surface temperature for a period, e.g. "july"
    ice=False,             # optional: polar ice at its winter maximum
    crops=None,            # optional: e.g. ["wheat", "rice:#18b5a4"]
    elevation=False,       # optional: land coloured by height, hill-shaded
    land_cover_classes=None,  # optional: e.g. ["forest"]; implies land_cover=True
    ndvi=None,             # optional: e.g. "2026-07" or "january" (not with land cover)
    soil_classes=None,     # optional: e.g. ["Chernozems", "PZ"]; implies soil=True
    soil_property=None,    # optional: e.g. "ph" with soil_depth="0-5cm" (not with soil)
    koppen_classes=None,   # optional: e.g. ["Cfb"] or ["Cs"]; implies koppen=True
    trewartha_classes=None,  # optional: e.g. ["Do"] or ["C"]; implies trewartha=True (not with koppen)
    up=0,                  # optional: compass bearing at the top (180 = south up)
    circles_from=None,     # optional: (lat, lon) to measure the distance circles from instead
    distance_circles=True, # optional: False leaves the distance circles out
)
```

## Testing

Run the test suite (requires the `[dev]` extra or `pip install pytest`; the web app's tests run when FastAPI is installed and are skipped otherwise):

```powershell
python -m pytest
```

The suite runs fully offline. `tests/conftest.py` blocks any connection to a non-local host, points Cartopy at an empty data folder, and replaces the Natural Earth land/ocean features with small stand-ins, so a test that accidentally reaches the network fails instead of quietly passing on an online machine.

## Notes

- Zoom is capped at level `4` for the whole globe to avoid excessive tile downloads. A radius map covers less ground, so it takes finer tiles: its default zoom gives about a dozen tiles across the map at its latitude, and `--zoom` may go one higher (see "Radius Maps").
- Web map tiles stop at about ±85° latitude, so a small disc around each pole shows the plain fallback colours. On OSM they match the tiles and are hard to see; on `google_satellite` and `nasa` the disc is visible. `--ice` covers both discs with ice; without it this is a known cosmetic limitation.
- The default 300 DPI gives a ~6,000 px image. Map imagery carries roughly 2,000–4,000 px of real detail at zoom 3–4, so higher DPIs mostly upscale it; they still make text, circles, routes and the keys sharper (`--dpi 600` gives ~12,000 px).
- Output uses `bbox_inches="tight"` and `transparent=True`, so the resulting PNG has minimal padding around the globe.
- Google tile backends depend on Cartopy tile services and may be subject to provider availability or usage limits.
- If some or all map tiles fail to download, the map is still saved: missing tiles are left transparent so the fallback land/ocean features show through, and a warning says how many tiles failed.
- OSM tiles are cached in `~/.cache/ortho_tiles/osm/` and reused for 7 days, as the OSM tile usage policy asks; NASA tiles are cached in `~/.cache/ortho_tiles/nasa/` and `nasa_night/` for a year; terrain and vegetation tiles in `terrarium/` and `gibs/` for good. Use `--cache-dir` to change the location or `--no-cache` to skip it. Failed downloads are never cached. Google tiles are never cached, because Google's terms don't allow it. In code, pass `tile_cache_dir=configure_tile_cache()` to `generate_orthographic_map`; the default is no cache.
- When a pre-defined city is selected, a red marker and bold label are drawn at the centre point. Custom-coordinate renders omit the marker.
- Every render includes two concentric geodesic circles at 2,500 km and 5,000 km from the centre, computed on the WGS-84 ellipsoid. The circles are drawn as white dashed rings with distance labels at the top of each circle as drawn on the globe. A radius map draws round-numbered circles inside its edge instead.

## Turning the Globe

By default north is up. `--up BEARING` (`up=` in the API, or the interactive
prompt) turns the globe so another compass direction points up, measured in
degrees clockwise from north: `--up 180` gives a south-up map, `--up 90` puts
east at the top. `--up-toward PLACE` works out the bearing for you, so the
direction toward a city or a `LAT,LON` point is at the top, along the great
circle:

```powershell
python ortho.py --city sydney --up 180
python ortho.py --city lisbon --provider google_satellite --up-toward "New Delhi"
python ortho.py --city london --up-toward 21.42,39.83    # Mecca: the qibla, 119°
```

The New York sample above was rendered with
`python ortho.py --config recipes/nyc_toward_london.toml`
(`--city nyc --provider nasa --up-toward london`).

- The globe turns; the text does not. The city label, distance-ring labels,
  keys and credits stay upright, and the ring labels stay at the top of their
  rings. Tiles, routes, areas, ice, crops and climate colours turn with the globe.
- Place names that are part of the OSM or Google road-map tiles are baked into
  the imagery, so they turn with it and read sideways or upside down. Satellite
  imagery (`nasa`, `google_satellite`) has none, which makes it the best fit for
  turned globes.
- With `--both-hemispheres` the far globe turns to match: the point at the top
  of the near globe is also at the top of the far one, so the pair stays two
  halves of one Earth.
- With `--beside`, `--up` turns both globes by the same bearing, and
  `--up-toward` turns each toward the place from its own centre.
- Turned globes are computed on a sphere of the Earth's mean radius rather than
  the WGS-84 ellipsoid, a difference far below a pixel at globe scale. North-up
  renders are unchanged.
- Auto-named files get an `_up<bearing>` suffix, for example
  `orthographic_map_sydney_osm_z3_up180.png`.
- For a point with a negative latitude, join the value with `=` so it isn't
  read as another option: `--up-toward=-33.9,151.2`.

## Both Hemispheres

An orthographic globe shows one hemisphere. `--both-hemispheres`
(`both_hemispheres=True` in the API, or the interactive prompt) adds a second
globe to the right, centred on the antipode: the point directly opposite the
centre, at the negated latitude and 180° of longitude away. Together the two
globes show the whole Earth, the way old atlases drew the world as a pair of
hemispheres.

```powershell
python ortho.py --city lisbon --provider google_satellite --route routes/portuguese_explorers.geojson --route-legend --both-hemispheres
```

- The far globe marks the antipode with a hollow ring ("Antipode of Lisbon").
- Its distance circles are still measured from the chosen city, at 15,000 and
  17,500 km, which mirrors the 5,000 and 2,500 km rings on the near globe.
- Routes, areas and the climate overlay are drawn on both globes, so a
  route that goes over the horizon of one carries on in the other. Each key is
  drawn once, centred under the pair, and the credits sit under the right globe.
- Auto-named files get a `_hemispheres` suffix, for example
  `orthographic_map_lisbon_google_satellite_z3_hemispheres.png`.
- The image is twice as wide (about 9,600 px at 300 DPI), so `--dpi` is
  capped at 600 instead of 1,200 to keep the same memory budget. Google
  renders download tiles for both globes.

## Two Places Side by Side

`--beside PLACE` (a city or `LAT,LON`; `beside=(lat, lon)` in the API,
`beside` in recipes, "Second globe beside it" in the web app) draws the second
globe centred on any place instead of the antipode, to compare two parts of the
world at the same scale:

```powershell
python ortho.py --city lisbon --beside tokyo
python ortho.py --city lisbon --beside tokyo --radius 2500 --koppen
python ortho.py --config recipes/tokyo_lisbon.toml   # the Tokyo and Lisbon sample above
```

- Each globe is marked with its place's name (a `LAT,LON` place is left
  unmarked, as the first globe's is) and gets its own distance circles, from
  its centre or from `--circles-from`.
- Every layer is drawn on both globes; each key is drawn once, centred under
  the pair.
- With `--radius`, both globes show that radius. The default zoom suits the
  centre nearer the equator, where the tiles cover the most ground.
- `--up` turns both globes by the same bearing; `--up-toward` turns each toward
  the place from its own centre.
- Auto-named files join the two places: `orthographic_map_lisbon_and_tokyo_osm_z3.png`.
- As with both hemispheres, `--dpi` is capped at 600. It cannot be combined
  with `--both-hemispheres`, and the interactive prompt does not offer it.

## Route Overlays

Pass one or more GeoJSON files with `--route` (CLI), at the interactive prompt,
or as `routes=` (API) to draw routes and areas on the globe. Add `--route-legend`
for a key naming each one:

```powershell
python ortho.py --city lisbon --route routes/gibraltar_ascension_falklands.geojson
python ortho.py --city lisbon --route routes/portuguese_explorers.geojson --route-legend
```

```python
from ortho import generate_orthographic_map
from routes import load_routes

generate_orthographic_map(
    lat=38.7223, lon=-9.1393, output_filename="lisbon.png",
    routes=load_routes("routes/portuguese_explorers.geojson"),
    route_legend=True,
)
```

Files given later are drawn on top of earlier ones. Route files are validated
before any tiles are fetched, so a missing or malformed file fails immediately.

### File format

Each file is a GeoJSON `FeatureCollection`, `Feature` or bare geometry with
`[lon, lat]` coordinates in degrees:

- `LineString` and `MultiLineString` are drawn as lines. Segments between
  vertices follow the great circle, so sparse routes still curve correctly.
- `Polygon` and `MultiPolygon` are drawn as translucent filled areas beneath
  the lines. Holes are supported.

Styling uses the [simplestyle-spec](https://github.com/mapbox/simplestyle-spec)
property names, so files also render sensibly on GitHub and
[geojson.io](https://geojson.io):

| Property | Meaning | Default |
|---|---|---|
| `name` | Route name, used in logs and as the key label | file name |
| `legend` | Short label for the route key; features with the same label share one entry | `name` |
| `stroke` | Line or outline colour (any Matplotlib colour) | `#ff0000` |
| `stroke-width` | Line or outline width in points | `2` |
| `fill` | Area fill colour (polygons only) | the `stroke` colour |
| `fill-opacity` | Area fill opacity, 0–1 (polygons only) | `0.35` |

```json
{
  "type": "Feature",
  "properties": {"name": "Vasco da Gama: sea route to India", "legend": "Vasco da Gama, 1497–1499",
                 "stroke": "#ff3b30", "stroke-width": 2.5},
  "geometry": {"type": "LineString", "coordinates": [[-9.14, 38.71], [-9.6, 38.5], [-15.97, 28.3]]}
}
```

### Route key

With `--route-legend` (`route_legend=True` in the API), a key below the globe
lists each label next to a swatch: a line for routes, a filled box for areas.
Entries run top layer first, the reverse of drawing order, as in a GIS layer
list. Give features a shared `legend` label to group them, for example all
trade routes as "Trade routes"; without one, every feature gets its own entry.
When a climate overlay is on, the route key goes below the climate key.

### Bundled route files

| File | Colour | Contents |
|---|---|---|
| `gibraltar_ascension_falklands.geojson` | red | Gibraltar to Ascension Island and the Falklands |
| `portuguese_explorers.geojson` | one per voyage | Henry the Navigator's captains, Diogo Cão, Bartolomeu Dias, Vasco da Gama, Pedro Álvares Cabral, Pêro da Covilhã, Gaspar Corte-Real, and Magellan and Elcano (1415–1522) |
| `viking_homelands.geojson` | brown | Viking Age Denmark, Norway, and the Swedes and Geats |
| `viking_settlements.geojson` | green, blue | Norse settlements (Danelaw, Dublin, Norse Scotland and Man, Faroes, Iceland, Greenland, Normandy, Kievan Rus') in green; the Normans in southern Italy and Sicily in blue |
| `viking_trade.geojson` | orange | Dnieper and Volga river routes, Baltic and North Sea trade |
| `viking_raids.geojson` | red | Lindisfarne, Iona and Dublin, Paris, Iberia and the Mediterranean, East Anglia, the Caspian |
| `viking_exploration.geojson` | purple | Faroes, Iceland and Greenland, Vinland, Ohthere's White Sea voyage |
| `zheng_he_voyages.geojson` | one per stage | Zheng He's treasure fleets, 1405–1433: Nanjing to Calicut by Champa, Java, Palembang, Malacca, Semudera and Ceylon (red, all seven voyages); on to Hormuz (yellow, from the fourth); Dhofar, Aden, Mogadishu, Brava and Malindi (green, from the fifth); squadrons to Siam, Bengal and Jeddah for Mecca (white) |

The Portuguese, Viking and Zheng He files carry `legend` labels, so they make
compact keys. The four historical sample maps above were rendered with these
recipes (see "Recipe Files"):

```powershell
python ortho.py --config recipes/portuguese_voyages.toml
python ortho.py --config recipes/viking_routes.toml
python ortho.py --config recipes/portuguese_hemispheres.toml
python ortho.py --config recipes/zheng_he_voyages.toml
```

The Viking recipe, for example, stands for
`--lat 62 --lon 15 --route routes/viking_homelands.geojson --route routes/viking_settlements.geojson --route routes/viking_trade.geojson --route routes/viking_raids.geojson --route routes/viking_exploration.geojson --route-legend --ice`.

The ice on the Viking map is today's winter maximum, not the Viking Age's.

All seven of Zheng He's voyages sailed the same route from Nanjing to Calicut,
so the Zheng He file draws it once and adds the later voyages' extensions as
their own layers, rather than seven lines on top of each other. The ports follow
the fleet's own inscriptions at Liujiagang (1431) and Changle (1432) and Ma
Huan's account of the voyages; the courses between them are approximate. The
Nanjing leg follows the Yangtze, the Palembang leg the Musi, the Siam leg the
Chao Phraya up to Ayutthaya, and the Mecca leg goes overland from Jeddah.

The routes are approximate, drawn through documented landfalls and checked so
that sea legs stay off land. The homeland and settlement areas are approximate
outlines clipped to the [Natural Earth](https://www.naturalearthdata.com/)
1:10m coastline (public domain).

## Radius Maps

`--radius` (`radius_km=` in the API, or the interactive prompt) zooms in: the
map shows only the area within that distance of the centre, 2,500 km when no
distance is given, as a disc filling the same frame as the whole globe.

```powershell
python ortho.py --lat 46 --lon 10 --radius --elevation
python ortho.py --city tokyo --radius 800 --up 30
python ortho.py --config recipes/alps_radius.toml
```

- **Any radius from 100 to 10,000 km.** It is still an orthographic view of
  the globe, so a circle on the ground around the centre is a circle on the
  map, and the edge of the disc is exactly the chosen distance. 10,000 km is
  about a quarter of the way round the Earth: the whole hemisphere again.
- **Finer tiles.** With less ground to cover, the default zoom puts about a
  dozen tiles across the map. Web Mercator tiles shrink away from the equator,
  so it depends on the latitude too: for 2,500 km, zoom 7 near the equator and
  6 at 45°; 8 for 1,000 km and 11 for 100 km near the equator (NASA imagery
  stops at 8). That is some 150–450 tiles the first time. `--zoom` can go one
  higher, for about 3.5 times as many tiles.
  The elevation layer follows the map tiles' zoom (up to 10), shading
  less steeply as the tiles get finer, and uses the Natural Earth 1:10m
  coastline from zoom 7. The land cover and NDVI tiles go up to zoom 8 and 7.
- **Distance circles** fall at round steps inside the edge: every 500 km for
  2,500 km, every 250 km for 800 km.
- **Circles from another place.** `--circles-from PLACE` (a city or `LAT,LON`)
  measures the circles from there instead, marked with a hollow ring, and spaces
  them to cover the map as seen from it. The Zheng He sample measures them from
  Nanjing, where the fleets set out, at 2,500, 5,000 and 7,500 km. It works on
  whole-globe maps too, with the usual 2,500 and 5,000 km rings.
- The climate, soil, crop, ice, wind and route layers are drawn as usual; the
  climate and soil grids (7–15 km) look blocky on small radii.
- It works with `--up`, `--up-toward` and `--beside` (both globes show the
  radius), but not with `--both-hemispheres`.
- Auto-named files get an `_r<km>km` suffix, for example
  `orthographic_map_tokyo_osm_z8_r800km.png`.

## Prevailing Winds

`--wind` (`wind=` in the API, or the interactive prompt) draws the prevailing
surface winds as arrows: the mean wind 10 m above the ground over 1991–2020,
from the NCEP/NCAR Reanalysis, for the whole year (the default), a season or
a month.

```powershell
python ortho.py --lat 15 --lon -30 --wind
python ortho.py --lat 15 --lon 75 --wind jja
python ortho.py --config recipes/wind_january.toml
```

- **Periods.** `year`; the meteorological seasons `djf` (December–February),
  `mam`, `jja` and `son`; or a month by name or number (`july`, `jul`, `7`).
  A season averages its months, each weighted by its number of days. Seasons
  matter where the wind turns: the monsoons of South Asia, West Africa and
  northern Australia blow one way in summer and the other in winter, and over
  the year they nearly cancel out.
- **Arrows.** Each points the way the wind blows (downwind), and its length
  is the mean wind's speed, with arrows for 2, 5 and 10 m/s in the key. They
  are spaced evenly across the map, about 40 across, at any radius.
- **Colours: steadiness.** The mean wind's speed divided by the average wind
  speed. Where the wind blows from one direction nearly all the time the two
  are close and steadiness nears 1: the trade winds of Hawaii score about 0.9.
  Where it comes from every direction in turn the mean wind cancels out:
  London's westerlies score about 0.3–0.45. The key has four bands:
  variable (below 0.3), changeable, steady and very steady (0.8 and above).
- **Data.** NOAA PSL's long-term monthly means of the NCEP/NCAR Reanalysis 1:
  the eastward and northward wind and the mean wind speed at 10 m, on a
  1.9° grid (about 200 km), interpolated to the arrows. Three files of about
  1 MB, downloaded once and cached in `~/.cache/ortho_tiles/wind/`. The
  average speed is of daily mean winds, so gusts and the daily land and sea
  breezes are not counted against steadiness.
- **Limits.** At 200 km the grid smooths out local winds that follow valleys
  and coasts, like the mistral or the bora, so on a small radius map the
  arrows show the regional wind, not the local one.
- The arrows sit above the climate, soil, crop and ice layers and below the
  routes and distance circles. The credit line is *Winds: NCEP/NCAR
  Reanalysis 1, 1991–2020 means, NOAA PSL*.

## Climate Means

`--temperature`, `--precipitation` and `--humidity` (the same keywords in the
API, or the climate prompt) colour the land by its mean climate over 1981–2010,
from CHELSA v2.1, for the whole year (the default), a season or a month:

```powershell
python ortho.py --lat 45 --lon 70 --temperature january
python ortho.py --lat 5 --lon 100 --precipitation
python ortho.py --lat 30 --lon 20 --humidity jja
python ortho.py --config recipes/temperature_july.toml
```

- **Periods.** As for `--wind`: `year`, a season (`djf`, `mam`, `jja`, `son`)
  or a month by name or number. Temperature and humidity average the months,
  each weighted by its number of days; precipitation adds them up.
- **Temperature.** The daily mean air temperature 2 m above the ground, in
  17 bands of 5 °C from below −40 °C to 35 °C and above: purples and blues
  below freezing, greens and yellows for the mild bands, oranges and reds for
  the hot ones. It is the mean of day and night, so it runs well below the
  daily highs of a forecast.
- **Precipitation.** Rain and snow (as water) in mm, in ten bands from brown
  (dry) through green to blue and purple (wet). The bands follow the period:
  10–400 mm for a month, 25–1,200 mm for a season, 100–4,000 mm for the year.
  CHELSA corrects its precipitation with GPCC's rain gauges.
- **Humidity.** The daily mean relative humidity near the ground, in nine
  bands of 10 % from below 20 % (brown) to 90 % and above (blue).
- **Land only.** CHELSA maps the land, so the sea is left to the base map, cut
  along Natural Earth's coast (the 1:10m coast from zoom 7) with its lakes left
  out. `--temperature-over-sea` and `--sea-temperature` colour the sea too (see
  "Temperatures over the Sea").
- **Data.** The CHELSA files are 1 km Cloud-Optimised GeoTIFFs; as for the
  Trewartha layer, only the ~15 km copy inside each is read, 2–5 MB per month
  and variable, downloaded the first time a map needs it and cached in
  `~/.cache/ortho_tiles/climate/`. A month's map needs one file, a season's
  three and the year's twelve. The grid is resampled smoothly (bilinear) to
  the map's pixels, up to 4 times finer, so zoomed-in maps (see "Radius Maps")
  have no blocky cells, though they show no detail finer than ~15 km.
- **Layers.** Only one climate layer is drawn at a time, a classification or a
  mean; its opacity is `--climate-alpha` (default 0.7 for a mean). It sits
  where the classifications do: above the elevation and vegetation layers,
  so `--elevation` shows the relief through it, and below soils, crops, ice,
  winds and routes. `--precipitation jja --wind jja` shows a monsoon's rain
  and the wind that brings it. The credit line is *Climate means: CHELSA
  v2.1, 1981–2010 (Karger et al. 2017), CC0*.

## Temperatures over the Sea

CHELSA maps the land only. Two options colour the sea and lakes too, in the
same 5 °C bands as `--temperature`:

```powershell
python ortho.py --lat 20 --lon -40 --temperature july --temperature-over-sea
python ortho.py --lat 20 --lon -40 --sea-temperature july
python ortho.py --lat 20 --lon -40 --temperature july --sea-temperature july
python ortho.py --lat 50 --lon 0 --koppen --sea-temperature january
```

- **The air over the sea.** `--temperature-over-sea` (`temperature_over_sea=True`
  in the API, "Over the sea too" in the web app) carries `--temperature` out over
  the sea: the mean air temperature 2 m above the surface over 1981–2010, CHELSA's
  period, from the NCEP/NCAR Reanalysis (the winds' source). Land and sea are one
  image, cut along one coast, under one key. The reanalysis grid is coarse, about
  1.9° (~210 km), smoothed onto the map. Over the open sea, where temperatures
  change slowly, that shows no blocks; along coasts each cell blends in some land,
  so the air off a hot desert coast in summer, or off Siberia in winter, can come
  out a band warmer or colder than the water nearby.
- **The sea surface.** `--sea-temperature [PERIOD]` (`sea_temperature=` in the API,
  "Sea surface temperature" in the web app) colours the sea by its mean surface
  temperature over 1991–2020, from NOAA's OISST v2.1 at 0.25° (~28 km). Currents
  show clearly: the warm Gulf Stream, the cold upwelling off Peru and north-west
  Africa. It goes with any climate layer except `--temperature-over-sea`. With
  `--temperature` over the same period the two share one image and one key ("Air
  temperature on land (1981–2010) and sea surface temperature (1991–2020)"); over
  another period, or beside another layer, it gets its own key. Its opacity is
  `--climate-alpha` when given, else 0.7.
- **Surface or air.** Over open water they agree to about a degree; the air is
  typically ~1 °C cooler. Over sea ice they part: the sea surface cannot cool much
  below −1.8 °C, where sea water freezes, while the air over the sea ice averages
  about −17 °C in January. `--ice` draws the ice over either.
- **Lakes.** Both colour lakes as well as the sea: the reanalysis has a value
  everywhere, OISST only for the larger lakes (the Great Lakes, for one).
- **Data.** Each is one file from NOAA PSL, downloaded the first time a map needs
  it and cached in `~/.cache/ortho_tiles/sea/`: OISST's monthly means are ~46 MB,
  the reanalysis's ~0.5 MB. OISST leaves a gap of a cell or two along the coasts,
  filled from the nearest sea so the colours reach the coastline.
- The credit lines are *Sea surface temperature: NOAA OISST v2.1, 1991–2020
  means, NOAA PSL* and *Air temperature over the sea: NCEP/NCAR Reanalysis 1,
  1981–2010 means, NOAA PSL*.

## Historical Borders

`--borders WHEN` (`borders=` in the API, or the interactive prompt) draws the
states of a moment in history: filled in muted colours that differ from their
neighbours', outlined, and named where there is room, with a key naming the
largest on the map. Give a year (`1500`; `-500`, `500 BC` or `500 BCE` before
the common era) or, for CShapes, a day (`1914-08-01`). A year alone means 1 July
of it.

```powershell
python ortho.py --city Lisbon --provider osm_plain --borders 1500 --route routes/portuguese_explorers.geojson
python ortho.py --lat 40 --lon 20 --borders 117 --radius 3000
python ortho.py --lat 5 --lon 20 --provider osm_plain --borders 1914-07-01
python ortho.py --lat 50 --lon 15 --borders 1938 --borders-source cliopatria
```

The samples above are `python ortho.py --config recipes/borders_1500_voyages.toml`
and `python ortho.py --config recipes/africa_1914.toml`. Borders go well with
the routes: the voyages over the states they sailed past.

Two sources, chosen by `--borders-source` (default `auto`: CShapes for 1886–2019,
Cliopatria otherwise):

- **[Cliopatria](https://github.com/Seshat-Global-History-Databank/cliopatria)**
  (Seshat Global History Databank, v0.2.1, CC BY 4.0): over 1,600 states and
  empires from 3400 BCE to 2024, each with the years its borders held. An
  empire's parts share its colour (British Raj, British Africa and the British
  Colonial Empire are all the British Empire's), and the key names the empire.
  Coverage follows the historians' sources: it is dense for Eurasia and North
  Africa and thin elsewhere before the colonial era, so in 1500 much of
  sub-Saharan Africa and the Americas has no states drawn. Snapshots are taken
  every few decades, not every year. The download is ~46 MB once (cached as
  ~14 MB).
- **[CShapes 2.0](https://icr.ethz.ch/data/cshapes/)** (ETH Zurich): the
  independent states and their colonies, protectorates, mandates and occupied
  lands from 1886 to 2019, to the day the borders changed. Each dependency is
  drawn in a lighter shade of its ruler's colour, so 1914's empires read at a
  glance. Territories carry their CShapes name throughout, which is usually the
  present one (*Zimbabwe (Rhodesia)*, *Mali*), and border changes under
  10,000 km² are not recorded. It is read from the copy in the `cshapes` R
  package on CRAN (~4 MB), the only one that records each territory's status
  and ruler.

- **Names** are placed in the middle of what shows of each state, larger for
  bigger states; a state covering less than 0.2% of the map is not named, nor is
  one whose name would overlap another's or cross the globe's edge. The key
  lists the 10 largest states or empires on the map in their colours.
- **Layering.** The borders are drawn over the climate, biome and soil colours
  and under crops, ice, winds and routes; `--borders-alpha` sets their opacity
  (default 0.55). The plain map (`osm_plain`) keeps the base map's own borders
  and labels quiet underneath.
- **Licences.** Cliopatria is CC BY 4.0. CShapes 2.0 states no licence of its
  own (the article describing it is CC BY-NC 4.0; the R package that carries the
  data is GPL ≥ 2). Neither is shipped here: each is downloaded on first use, and
  every map carries the credit line.

## Ocean Depth and the Ice Age Coast

`--ocean-depth` (`ocean_depth=True`) colours the sea floor by depth in eight
bands, from the pale continental shelves to the trenches below 6,000 m, and
shades it, so ridges, seamounts and canyons stand out. `--ice-age`
(`ice_age=True`) draws the last ice age: at its peak, about 20,000 years ago, so
much water was locked in ice sheets that the sea stood some 120 m lower
(estimates run to 134 m). It shows the land that left dry, with its coastline
(Doggerland joined Britain to Europe, Beringia joined Asia to America, Sundaland
joined Borneo, Sumatra and Java to Asia), and the ice sheets themselves, white
within their margins: the Laurentide and Cordilleran sheets over Canada and the
northern United States, Greenland, Iceland, the Eurasian sheet from Ireland across
Scandinavia and the Barents Sea, Patagonia and a larger Antarctica. The two
options go together or alone.

```powershell
python ortho.py --lat 55 --lon 3 --radius 2500 --ocean-depth --ice-age
python ortho.py --lat 0 --lon 115 --ice-age
```

The sample above is `python ortho.py --config recipes/ice_age_north_sea.toml`.

- **Data.** The same Terrarium terrain tiles as `--elevation`, whose sea
  heights come from ETOPO1 (NOAA). Lakes are left out, so the Caspian, below sea
  level, is not mistaken for dry sea floor.
- **Ice sheets.** PaleoMIST 1.0 (Gowan et al. 2021), the ice sheet margins
  20,000 years ago. The dataset is one 3.7 GB archive; only its four margin files
  (~2.3 MB) are read from it, by HTTP range requests, once. Mountain glaciers
  outside those sheets, such as the Alps' and the Himalaya's, are not in it.
  `--elevation-alpha` sets the opacity of the dry land and the sea floor.

## Plate Tectonics

Three layers, alone or together, with one key:

- `--plates`: the plate boundaries of Bird's PB2002 model, coloured by kind:
  spreading ridges and rifts, where plates move apart (red); subduction zones and
  collisions, where they meet (blue); transform faults, where they slide past
  (green). The large plates are named.
- `--earthquakes [MAGNITUDE]`: the earthquakes since 1900 of at least MAGNITUDE
  (default 6, from 5 to 9) from the USGS catalogue, sized by magnitude and
  coloured by depth: shallow (0–70 km), intermediate (70–300 km) and deep
  (300–700 km). The deep ones trace the plates sinking behind each trench.
- `--volcanoes`: the volcanoes active in the last 11,700 years (Smithsonian Global
  Volcanism Program), red where they have erupted since 1900.

```powershell
python ortho.py --lat 10 --lon 150 --plates --earthquakes --volcanoes
python ortho.py --lat 40 --lon 30 --earthquakes 5
```

The sample above is `python ortho.py --config recipes/ring_of_fire.toml`. The
earthquake and volcano lists are fetched again after 30 days, and an older copy
is used when the services cannot be reached. Magnitude 5 and over is about
110,000 earthquakes, downloaded in a dozen parts the first time.

## Ocean Currents

`--currents` draws the mean surface currents as arrows, from the NOAA Global
Drifter Program's climatology (Laurindo et al. 2017): the tracks of thousands of
drifting buoys since 1979, averaged to 1° cells. Arrow length grows with the
square root of speed, so slow currents show beside fast ones. The colour tells
warm currents from cold: the water's temperature against the average of its
latitude around the world, so the Gulf Stream and the Kuroshio are warm, and the
Labrador, Canary, Benguela, Humboldt and California currents cold.

```powershell
python ortho.py --lat 25 --lon -50 --currents
```

The 51 MB climatology is downloaded once and reduced to under 1 MB. The sample
above (with `--cyclones`) is `python ortho.py --config recipes/atlantic_currents_hurricanes.toml`.

## Rivers and Basins

`--rivers` draws Natural Earth's main rivers, wider the more important, and names
the longest on the map, each once, in English (the Danube, not the Donau; the
White Nile for its stretches with other names), leaving delta branches unnamed. `--basins` colours the river basins of 100,000 km² or more, the
land each great river drains, in colours that differ from their neighbours',
named and with a key; basins draining to an inland sink (the Okavango, Lake Chad,
the Aral Sea, the Dead Sea) are grey.

```powershell
python ortho.py --lat 0 --lon 20 --rivers --basins
python ortho.py --lat 50 --lon -95 --basins --basins-alpha 0.4
```

- **Data.** HydroBASINS level 4 (Lehner & Grill 2013, HydroSHEDS), nine regional
  files of ~55 MB once, merged into the ~190 basins of that size (cached ~5 MB).
  HydroBASINS has no names, so each basin takes the name of the main Natural
  Earth river at its mouth, with about 30 corrections where that rule picks a
  headwater or tributary (Jinsha for the Yangtze, Beaver for the Churchill), a
  delta branch, or a name in another language (Huang for the Yellow River); the
  great inland basins are named after their sinks (Lake Eyre, Lake Chad, the
  Tarim, the Okavango). HydroSHEDS counts the Caspian as sea, so the Volga
  basin is not grey; the coastal strips between the large basins are left blank.

The sample above is `python ortho.py --config recipes/africa_river_basins.toml`.

## Cyclones, Time Zones and Permafrost

- `--cyclones [SINCE]` draws the tracks of the tropical cyclones (hurricanes,
  typhoons, cyclones) from the year SINCE (default 1980) in IBTrACS, from
  tropical storm strength up, each step coloured by its Saffir-Simpson category.
  The tracks since 1980, ~22 MB, are downloaded once.
- `--time-zones` shades the standard-time zones in turn (half-hour zones in a
  third shade), outlines them and labels each with its offset from UTC; daylight
  saving time is not shown (Natural Earth).
- `--permafrost` colours the Northern Hemisphere's permafrost (Obu et al. 2019,
  2000–2016) by how continuous it is: continuous, discontinuous, sporadic and
  isolated patches; `--permafrost-alpha` sets its opacity. ~13 MB once.

```powershell
python ortho.py --lat 20 --lon 130 --cyclones 2000
python ortho.py --lat 75 --lon 90 --permafrost --time-zones
```

## Population Density

`--population [YEAR]` colours the land by people per km² in 2000, 2005, 2010,
2015 or 2020 (default 2020), from NASA's Gridded Population of the World (GPWv4,
CIESIN), in nine bands from under 1 to 1,000 and more. The tiles come from NASA
GIBS like the vegetation layer, at zoom 5 (finer on a radius map, up to zoom 7);
their colours are turned back into densities and redrawn in the bands of the key.
`--population-alpha` sets the opacity.

```powershell
python ortho.py --lat 25 --lon 90 --population
python ortho.py --lat 50 --lon 10 --radius 1500 --population 2000
```

The sample above is `python ortho.py --config recipes/asia_population.toml`.

## Livestock

`--livestock ANIMAL` colours the land by the head of one kind of livestock per
km² in 2015: `cattle`, `buffaloes`, `horses`, `sheep`, `goats`, `pigs`, `chickens`
or `ducks` (Gridded Livestock of the World v4, FAO). Mammals are drawn in bands
from 1 to 250 and more per km², poultry from 10 to 2,500 and more. Each animal's
map, ~10 MB, is downloaded once; `--livestock-alpha` sets the opacity.

```powershell
python ortho.py --lat 20 --lon 75 --livestock cattle
python ortho.py --lat -30 --lon 145 --livestock sheep
```

The sample above is `python ortho.py --config recipes/india_cattle.toml`.

## Malaria and Aedes Mosquitoes

`--malaria PARASITE` colours the land by the share of people carrying malaria
parasites, from the Malaria Atlas Project's 2026 release:

- **`pf`, *Plasmodium falciparum*:** the deadliest parasite, mostly in Africa.
  The rate is of children aged 2 to 10, the age at which surveys measure it.
  It is drawn in bands from 0.1% to 60% and more.
- **`pv`, *P. vivax*:** most common in Asia and the Americas. The rate is of
  people of all ages, and much lower, so its bands run from 0.1% to 7% and more.

`--malaria-year` picks the year, from 2000 to 2025 (default 2025), so you can
compare maps of different years. Below 0.1% the land is left clear. So are the cells MAP masks
out: sparsely populated parts of South America for *P. falciparum*, and very
low transmission for *P. vivax*. Each year's map is read at about 10 km cells
(~40 MB) once; `--malaria-alpha` sets the opacity.

`--aedes` draws where *Aedes aegypti* (red) and *Ae. albopictus* (blue), the
mosquitoes that carry dengue, chikungunya, Zika and yellow fever, were recorded
from 1958 to 2014 (Kraemer et al. 2015, about 42,000 records). Use `--aedes aegypti` or
`--aedes albopictus` for one of them. These are sightings collated from published
studies, not a map of risk: a country studied less shows fewer dots. The
records, ~0.3 MB a species, are read once from GBIF.

```powershell
python ortho.py --lat 5 --lon 20 --malaria pf
python ortho.py --lat 15 --lon 80 --malaria pv --malaria-year 2000
python ortho.py --lat 0 --lon -70 --aedes
python ortho.py --lat 25 --lon 110 --radius 3000 --aedes albopictus
```

The samples above are `python ortho.py --config recipes/africa_malaria.toml` and
`python ortho.py --config recipes/americas_aedes.toml`.

## Species Records

`--species NAME` draws where a species has been recorded in the wild, from the
billions of occurrence records gathered by [GBIF](https://www.gbif.org/):
museum specimens, surveys, and citizen science such as iNaturalist and eBird.

- **Any name:** a scientific name (`"Panthera leo"`, or misspelt as
  `"Vulpes vulpis"`) or a common one (`lion`, `"red fox"`, `"monarch butterfly"`).
  A common name goes to the taxon most sources call by it, so `tiger` is
  *Panthera tigris*, not the tiger muskellunge.
- **Any rank:** a genus or family works too (`Quercus` or `oak` for all oaks,
  `Felidae` for all cats). The key names the rank.
- **Cells, not dots:** the world is split into cells of about 20 km (0.18°), shaded
  by how many records each holds, from 1 to over 1,000. On a zoomed-in map each cell
  is drawn at its true size; on a whole globe each is widened just enough to see.
- **Wild records only:** fossils and zoo or garden specimens are left out.
- **Years:** `--species-years 1990-2020` (or one year, `2015`) keeps only the
  records of those years.

These are records, not a range map. Places with many naturalists, such as
Britain, the Netherlands and North America, have far more records than the
tropics, so a dark cell shows where people looked as much as where the species
lives. The name and the eight tiles of records (from a few KB to ~2 MB) are read
once and cached under `~/.cache/ortho_tiles/gbif/`; delete a cached file to
fetch newer records. The credit gives the date the records were read.

```powershell
python ortho.py --lat 0 --lon 20 --species lion
python ortho.py --lat 30 --lon 85 --species tiger
python ortho.py --lat 40 --lon -95 --species "monarch butterfly" --species-years 2020-2025
python ortho.py --lat 52 --lon 0 --radius 900 --species "red fox" --species-years 2000-2025
python ortho.py --lat 45 --lon 10 --species Quercus
```

The samples above are `python ortho.py --config recipes/africa_lion.toml` and
`python ortho.py --config recipes/britain_red_fox.toml`.

## Species Ranges

`--iucn NAME` draws where a species lives according to the
[IUCN Red List](https://www.iucnredlist.org/), or for birds
[BirdLife International](https://datazone.birdlife.org/): the range their experts
have mapped, rather than where people happened to record it. It pairs with `--species`:
`--iucn lion --species lion` draws the range with the GBIF records on top.

Neither's terms allow the range data to be passed on, so this project neither
downloads nor ships it. Get it yourself, free, for non-commercial use:

1. Make an account at [iucnredlist.org](https://www.iucnredlist.org/) and log in.
2. Either take a whole group (mammals, amphibians, reptiles, fishes …) from the
   [spatial data download](https://www.iucnredlist.org/resources/spatial-data-download)
   page, or search for a species or group and choose **Download → Range data –
   Polygons (SHP)**. IUCN asks how you will use the data, then emails a zip.
3. Put the zip, as it is, in `~/.cache/ortho_tiles/iucn/` (any number of zips),
   or point to it with `--iucn-data PATH`.

For birds, request **Species' distribution maps** from
[BirdLife's data zone](https://datazone.birdlife.org/species/requestdis). The zip
holds a GeoPackage (`BOTW_2025.gpkg`, ~9 GB) and BirdLife's checklist. Unzip the
GeoPackage into the same folder (SQLite cannot read inside a zip; a warning says
so if you forget) and leave the zip or the checklist there: the checklist gives
each bird's family, order, Red List category and English names, so
`--iucn osprey`, `--iucn "white stork"` and `--iucn Accipitridae` work. The names
in the GeoPackage are indexed once (about 20 seconds).

- **Any name:** a scientific name as IUCN spells it (`"Lynx lynx"`), a genus
  (`Panthera`), a family (`Felidae`) or an order. A common name (`lion`) or a
  synonym is looked up in GBIF and its scientific name used; a near miss
  (`"Panthera leon"`) is answered with the names it may mean.
- **Kinds of range:** each part is shaded by IUCN's codes. Extant (resident),
  breeding and non-breeding season and passage are solid; possibly extant and
  possibly extinct are hatched; introduced and vagrant have their own colours;
  where the species has died out is hatched grey. The key lists only the kinds
  the range has and gives a species' Red List category.
- **Read once:** the shapes are read straight from the zip (a species takes a
  few seconds, a large family up to a minute), merged by kind, simplified to
  about 1 km and kept under `~/.cache/ortho_tiles/iucn/ranges/`. Adding another
  group's zip keeps the ranges already read; replacing a zip reads its ranges
  afresh. Plants, amphibians, reptiles and the other groups work the same way
  (`--iucn "Picea omorika"`, `--iucn "Rana temporaria"`). Migratory birds show
  their breeding, non-breeding and passage ranges.

The credit names IUCN and the date of the data, or BirdLife and the version of
its maps. Both publishers' terms allow use for conservation, education and
research, but not commercial use or sharing the maps without their permission,
so this README has no sample image.

```powershell
python ortho.py --lat 10 --lon 30 --iucn lion --species lion
python ortho.py --lat 50 --lon 10 --radius 2500 --iucn "Lynx lynx"
python ortho.py --lat 40 --lon -100 --iucn Felidae
python ortho.py --lat 30 --lon -60 --iucn osprey
python ortho.py --lat 20 --lon 10 --iucn "white stork"
```

## Cities

`--cities` draws the world's big cities as they are built: the urban centres of
the [GHS Urban Centre Database 2025](https://human-settlement.emergency.copernicus.eu/ghs_ucdb_2024.php)
(European Commission, JRC). An urban centre is a continuous patch of 1 km cells
with at least 1,500 people per km² and 50,000 people in all, so it follows the
built-up metropolis rather than city limits: "Guangzhou" is the whole Pearl River
Delta with Shenzhen, Foshan and Dongguan (43 million people), "Essen" the Ruhr.

- **Shapes and names:** each centre is filled in red as its real outline; the
  largest are named, biggest first, above a dot, in bold from 10 million. A name
  that would overlap another or run off the globe is left out (at most 80).
- **How many:** on a whole globe, the centres of over a million people (some 500
  worldwide); on a closer map the bar falls with the radius, to GHSL's smallest
  centres of 50,000 within a few hundred kilometres. `--cities-min 250000` sets it.
- **Any year:** `--cities 1975` (or 1980 … 2025, and 2030 projected) shows each
  centre's extent and population that year. A town that later merged into a
  bigger centre is drawn as part of it, so the 1975 map shows the separate towns
  that became one metropolis.

The data (~35 MB, 11,686 centres) is downloaded once into
`~/.cache/ortho_tiles/cities/`; each year's outlines are converted to degrees and
simplified to about 200 m the first time that year is drawn.

GHSL's file lost every letter outside Latin-1 from its names: 329 came out as
`Gala?i`, `Klaip?da` or, for names in other scripts, `?????`. They are restored
once from [GeoNames](https://www.geonames.org) (places of 5,000 people and more,
~6 MB, CC BY 4.0) by matching each name against the places near its centre, a
`?` standing for one lost letter: `Galați`, `Klaipėda`, `Điện Biên Phủ`. A name
lost entirely takes the romanised name of the place it matches or lies within
~10 km of (`Gomel`); 75 small centres that match nothing are drawn but not
named. Names GHSL spelt without their accents get them too (`Brasov` becomes
`Brașov`, `Krakow` `Kraków`), except where the language has another script
(no `Chóngfú` for `Chongfu`). Romanian and Moldovan names take ș and ț with a
comma below. When cities, plants and ports are drawn together, the cities are
named first and the other names fit around them.

```powershell
python ortho.py --lat 25 --lon 100 --cities
python ortho.py --lat 51 --lon 5 --radius 800 --cities
python ortho.py --lat 10 --lon 20 --cities 1975
python ortho.py --lat 40 --lon -100 --cities 2030 --cities-min 500000
```

The samples above are `python ortho.py --config recipes/pearl_river_delta_1975.toml`
and `python ortho.py --config recipes/pearl_river_delta_2025.toml`.

## Statistics

`--indicator NAME` shades each country by a statistic from the World Bank, or
each region of Europe by one from Eurostat, in seven classes of about as many
areas each, with rounded breaks. Areas without a figure are hatched grey.

**Countries (World Bank).** The
[World Development Indicators](https://data.worldbank.org/indicator) hold about
1,400 yearly statistics for 217 economies since 1960. These have short names:

| Name | Statistic |
|---|---|
| `gdp-per-person`, `gdp-per-person-ppp`, `gdp` | GDP per person (US$, or at purchasing power parity), and in all |
| `population`, `population-density`, `population-growth`, `aged-65`, `urban` | People: how many, how dense, how fast growing, how old, how urban |
| `life-expectancy`, `fertility`, `infant-mortality`, `health-spending` | Health and births |
| `literacy`, `internet`, `electricity` | Literacy, internet use, access to electricity |
| `poverty`, `inequality`, `unemployment` | Living on under $3.00 a day, the Gini index, unemployment |
| `co2-per-person`, `renewable-energy`, `forest` | CO₂ per person, renewable share of energy, forest cover |
| `military-spending` | Military spending as a share of GDP |

Any other indicator works by its code, e.g. `--indicator SH.H2O.SMDW.ZS` (safely
managed drinking water); the key takes its title from the World Bank. Countries
are Natural Earth's at 1:50m, matched by ISO code.

**European regions (Eurostat).** `eurostat:NAME` maps a statistic by
[NUTS](https://ec.europa.eu/eurostat/web/nuts) region: the EU, EFTA and some
candidate countries, in about 240 NUTS 2 regions (Lombardy, Île-de-France) or
1,100 NUTS 3 districts. The rest of the world is left as it is.

| Name | Statistic | Regions |
|---|---|---|
| `eurostat:gdp-per-person` | GDP per person, in purchasing power standards | NUTS 2 (`-nuts3` for NUTS 3) |
| `eurostat:population`, `eurostat:population-density`, `eurostat:median-age` | People | NUTS 3 |
| `eurostat:life-expectancy` | Life expectancy at birth | NUTS 2 |
| `eurostat:unemployment`, `eurostat:youth-unemployment`, `eurostat:employment-rate` | Work | NUTS 2 |
| `eurostat:tertiary-education`, `eurostat:poverty-risk` | University-level education, risk of poverty or social exclusion | NUTS 2 |

Any Eurostat dataset with regions works too, with its other dimensions fixed:
`--indicator "eurostat:nama_10r_2gdp?unit=EUR_HAB"` (`&level=nuts3` for districts).
If a dimension is left open, the error lists its codes. The region shapes come
from Eurostat's GISCO service, in the NUTS version (2024, 2021 or 2016) that
matches the most regions with figures.

**Years.** By default each area shows its latest figure, and the key says which
year most of them are from. `--indicator-year 2000` asks for that year; an area
without a figure that year shows its latest from the nine years before, and the
key counts them. Answers are cached under `~/.cache/ortho_tiles/indicators/`;
the latest figures are asked for again after 30 days.

```powershell
python ortho.py --lat 10 --lon 20 --indicator life-expectancy
python ortho.py --lat 20 --lon -40 --indicator gdp-per-person --indicator-year 2000
python ortho.py --lat 30 --lon 80 --indicator co2-per-person
python ortho.py --lat 50 --lon 12 --radius 2200 --indicator eurostat:unemployment
python ortho.py --lat 48 --lon 8 --radius 900 --indicator eurostat:population-density
```

The samples above are `python ortho.py --config recipes/world_life_expectancy.toml`
and `python ortho.py --config recipes/europe_unemployment.toml`.

## Industry

`--industry SECTORS` draws industrial plants worldwide from
[Climate TRACE](https://climatetrace.org), which estimates the greenhouse-gas
emissions of each plant from satellite images and other data. Each plant is a
circle in its sector's colour, its area proportional to its emissions (CO₂
equivalent, last full year): a fair measure of size across very different
plants. The biggest are named, as long as the names don't overlap.

| Sector | Plants (worldwide) |
|---|---|
| `refineries` | Oil refineries (~730) |
| `petrochemicals` | Petrochemical plants: steam crackers making ethylene and propylene (~240) |
| `chemicals` | Chemical plants: ammonia, methanol, soda ash (~450) |
| `steel`, `aluminium`, `cement`, `glass`, `paper` | Steelworks, aluminium smelters and refineries, cement works, glassworks, pulp and paper mills |
| `coal-mines`, `copper-mines`, `iron-mines`, `bauxite-mines` | Mines |
| `power` | Fossil-fuel power stations (~11,600) |
| `oil-gas` | Oil and gas production, by basin (each circle a whole basin); what a country produces outside its named basins, nearly a third of the total, is one figure at one point, drawn as a hollow ring ("Russia, other basins") |

Groups: `petrochemical` (refineries, crackers and chemical plants), `heavy`
(those with steel, aluminium and cement), `mining`, and `all` (every sector
but the oil and gas basins, which would swamp the plants). The key counts the
plants of each sector in view. Circles grow a little on a closer map, so a
300 km disc still shows them well; the key's sizes always match the map.

Each sector is downloaded once from Climate TRACE's API (a few seconds; the
power stations ~20 s) and cached under `~/.cache/ortho_tiles/industry/`, read
again after 90 days. The figures are modelled estimates: Climate TRACE grades
its confidence for each plant, often "low" for single plants.

```powershell
python ortho.py --lat 30 --lon 60 --industry petrochemical
python ortho.py --lat 29 --lon -94 --radius 500 --industry refineries,petrochemicals,chemicals
python ortho.py --lat 51.3 --lon 5.5 --radius 300 --industry heavy
python ortho.py --lat 35 --lon 110 --industry steel,cement,power
python ortho.py --lat -15 --lon 25 --industry mining
```

The samples above are `python ortho.py --config recipes/world_petrochemical.toml`
and `python ortho.py --config recipes/rhine_ruhr_heavy_industry.toml`.

## Shipping, Ports and Air Routes

`--shipping` colours the sea by how busy it is with ships, from the
[Global Shipping Traffic Density](https://datacatalog.worldbank.org/search/dataset/0037580)
grids of the World Bank and the IMF: every ship's hourly AIS position from
January 2015 to February 2021, counted in cells of ~500 m. The lanes stand out
from the empty ocean, down to the traffic separation schemes of a strait.

| Kind | Ships | Download |
|---|---|---|
| `commercial` (default) | Cargo ships and tankers | 460 MB |
| `all` | Every ship | 510 MB |
| `fishing` | Fishing boats | 97 MB |
| `passenger` | Passenger ships and ferries | 22 MB |
| `oil-gas` | Oil and gas vessels | 21 MB |
| `leisure` | Leisure craft | 28 MB |

Each kind is downloaded once into `~/.cache/ortho_tiles/shipping/` and read
through once (about a minute, without unpacking its 9.8 GB grid) into 0.01°
tiles for close maps and a 0.04° grid for whole globes, ~85 MB in all; the zip
is then deleted. The colours rank the traffic on the map, from occasional (the
quieter half of the sea with ships is left clear) to the busiest lanes, as the
counts differ a hundredfold between seas: European and Chinese waters, seen by
many shore stations, are crowded everywhere. `--shipping-alpha` sets the
opacity.

`--air-routes` draws the airline network from
[OpenFlights](https://openflights.org/data): 67,663 routes between 3,321
airports on 548 airlines, as of June 2014 (OpenFlights' last update, so newer
routes are missing and some ended long ago). Each airport pair is one
great-circle arc, wider and stronger the more airlines fly it; routes only a
codeshare partner sells are left out. Give `all` for the whole network, or
airports by IATA code (`LHR`), ICAO code (`EGLL`) or city (`London`, all its
airports) to draw only the routes from them, each in its own colour, and
`airline:BA` (IATA or ICAO code, or the full name) for an airline's routes;
airports and airlines together give that airline's routes from those
airports. The airports are dotted by how many places they link to and the
busiest named. The three files (~4 MB) are downloaded once.

`--ports` draws the world's seaports from the IMF's
[PortWatch](https://portwatch.imf.org): 2,065 ports, each a navy dot sized by
the yearly average of ships calling there in 2019–2025, counted from their AIS
positions, and the busiest named (Singapore, Shanghai, Rotterdam … down to
Constanța's ~3,400 ships a year). Give a kind to size them by those ships only
and leave out the ports they don't call at: `container`, `tanker`, `dry-bulk`,
`general-cargo` or `roro` (car carriers and ferries). The table (~0.5 MB) is
downloaded once into `~/.cache/ortho_tiles/ports/` and again after a year. IMF
data may be reused with credit, as the map's credit line gives it (IMF
[terms](https://www.imf.org/external/terms.htm), "Special Terms for Statistical
Data"). PortWatch writes names in English (`Constantza`, `Odessa`); with
`--cities` the city's own name is shown instead where the two would overlap.

```powershell
python ortho.py --lat 20 --lon 70 --shipping
python ortho.py --lat 51.5 --lon 2 --radius 600 --shipping
python ortho.py --lat 60 --lon 0 --radius 1500 --shipping fishing
python ortho.py --lat 40 --lon 10 --air-routes all
python ortho.py --lat 45 --lon -20 --air-routes LHR
python ortho.py --lat 30 --lon 60 --air-routes "DXB, DOH, IST"
python ortho.py --lat 50 --lon 10 --air-routes airline:Lufthansa
python ortho.py --lat 25 --lon 118 --ports container --shipping --shipping-alpha 0.6
python ortho.py --lat 53 --lon 4 --radius 500 --ports tanker
```

The samples above are `python ortho.py --config recipes/world_shipping.toml`,
`recipes/channel_shipping.toml`, `recipes/world_air_routes.toml`,
`recipes/heathrow_air_routes.toml`, `recipes/east_asia_ports.toml`,
`recipes/europe_night.toml` and `recipes/romania_night.toml`.

## Polar Ice

`--ice` (`ice=True` in the API, or the interactive prompt) draws the ice at both
poles as it stands at the end of winter:

- **Sea ice at its winter maximum.** The Arctic usually peaks in March and the
  Antarctic in September, so the overlay uses NSIDC's monthly extent for those
  months: the area where at least 15% of the sea surface is ice. By default it
  uses the latest published maximum of each (the credit line names the months
  shown); `--ice-year 2012` picks a year instead, back to 1979.
- **Permanent land ice.** The Greenland and Antarctic ice sheets, the Antarctic
  ice shelves and the Arctic ice caps, from Natural Earth.

```powershell
python ortho.py --city lisbon --provider google_satellite --both-hemispheres --ice
```

Both fills are opaque, sea ice a shade bluer than land ice, so they also cover
the plain disc the map tiles leave around each pole. The sea ice outline follows
NSIDC's 25 km grid, smoothed slightly. The extent files are small (under 100 KB)
and are cached in `~/.cache/ortho_tiles/sea_ice/`; offline, the newest cached
year is used. If no data can be had, the map is saved without the ice and a
warning says why.

## Elevation

Pass `--elevation` (CLI) or `elevation=True` (API) to colour the land by
height in classic atlas bands (green lowlands, through tan and brown, to
grey and white above 4,000 m), with hill shading lit from the north-west and
a key below the globe.

```powershell
python ortho.py --lat -15 --lon -65 --elevation
python ortho.py --config recipes/andes_elevation.toml
```

- **Data.** The open [Mapzen terrain tiles](https://registry.opendata.aws/terrain-tiles/)
  on AWS: web-map tiles whose colours encode height, from GMTED2010 and SRTM
  (USGS) on land and ETOPO1 (NOAA) at sea. The layer uses zoom 5, about 5 km
  a pixel at the equator: some 500 tiles (~50 MB) for a hemisphere the first
  time, cached in `~/.cache/ortho_tiles/terrarium/` for good, since they
  never change.
- **Layering.** The layer sits on the map tiles (at 80% opacity, set with
  `--elevation-alpha`) and under the climate, crop, ice and route layers.
  The sea and lakes are left to the base map; land is told from sea with
  the Natural Earth outline, so land below sea level, like the Caspian
  Depression or the Dead Sea shore, is still coloured.
- **Ice sheets.** From zoom 5 the tiles give the rock under the Greenland and
  Antarctic ice (central Greenland reads −50 m); near the poles the ice
  surface from zoom 4 is used instead (about 3,200 m at Greenland's summit).
  Ice shelves are not land in Natural Earth, so they show the base map.
- **Limits.** Like the other web-map tiles, the terrain tiles stop at 85°N
  and 85°S. A tile that fails to download leaves the base map showing there,
  with a warning.

## Vegetation

Two MODIS vegetation layers from NASA, one at a time, drawn above the
elevation layer and below the climate and soil colours:

```powershell
python ortho.py --lat 10 --lon 20 --landcover
python ortho.py --lat 50 --lon -95 --landcover-class forest --landcover-class cropland --landcover-year 2010
python ortho.py --lat 35 --lon 30 --ndvi january
python ortho.py --lat 35 --lon 30 --ndvi july
```

**Land cover** (`--landcover`, `land_cover=True`) colours the land by its MODIS
land cover class (MCD12Q1, the 17 IGBP classes, 500 m) for any year from 2001 to
2024 (`--landcover-year`, default the latest), with a key. `--landcover-class`
shows only some, by group or by the start of a class name; it turns the layer on
by itself:

| Group | Classes |
|---|---|
| `forest` | Evergreen needleleaf, evergreen broadleaf, deciduous needleleaf, deciduous broadleaf and mixed forests |
| `shrubland` | Closed and open shrublands |
| `savanna` | Woody savannas and savannas |
| `grassland`, `wetland`, `urban`, `ice`, `barren` | Grasslands, permanent wetlands, urban and built-up lands, permanent snow and ice, barren |
| `cropland` | Croplands and cropland/natural vegetation mosaics |

Water is left to the base map. MODIS counts tree cover of 10–60% as "woody
savanna", so much of the boreal forest of Canada and Siberia is woody savanna
rather than forest; add `--landcover-class savanna` to see it. The North America
sample above is `python ortho.py --config recipes/north_america_landcover.toml`
(`--lat 45 --lon -100 --landcover-class forest --landcover-class cropland`).

**NDVI** (`--ndvi MONTH`, `ndvi=`) shows how green the vegetation was in one
month, from 0 (bare ground) to 1 (dense, growing vegetation): the MODIS Terra
monthly Normalized Difference Vegetation Index, from March 2000 to about two
months ago. Give `YYYY-MM`, or a month name or number for its latest year
(`--ndvi january`, `--ndvi 7`). Comparing a January and a July map shows the
seasons: northern Europe and Russia go from bare and snow-covered to deep green,
the Sahel greens with the summer rains, and southern Africa is greenest in
January, its summer. Snow-covered and polar-night land has no NDVI and shows the
base map. The sample above is the two recipes `ndvi_january.toml` and
`ndvi_july.toml` (`--lat 35 --lon 30 --ndvi 2026-01`, then `2026-07`), side by side.

- **Data.** Both come from NASA's Global Imagery Browse Services (GIBS) as map
  tiles at zoom 5 (~5 km), about 500 tiles of 10–40 KB for a hemisphere,
  cached in `~/.cache/ortho_tiles/gibs/`.
- **GIBS quirks.** For some tiles GIBS sends a true-colour satellite image
  instead of the layer (seen for NDVI where a month has no data) or a server
  error; those are retried twice, then left clear and not cached. Its tiles at
  the eastern edge (168.75°E–180°) are missing at zoom 5 and stay clear too.
- `--vegetation-alpha` sets the layer's opacity (default 0.7).

## Biomes, Ecoregions and Realms

Pass `--biomes` (CLI) or `biomes=True` (API) to colour the land by its biome, the
kind of natural community its climate and history support (tropical rainforest,
savanna, desert, tundra …), as mapped by
[RESOLVE Ecoregions 2017](https://ecoregions.appspot.com/) (Dinerstein et al.
2017), the update of WWF's *Terrestrial Ecoregions of the World*. A key below
the globe names the biomes on the map, in RESOLVE's colours.

```powershell
python ortho.py --lat 5 --lon 20 --provider osm_plain --biomes
python ortho.py --lat 10 --lon 100 --biome-class mangroves --biome-class "tropical moist"
python ortho.py --lat -19 --lon 46.5 --radius 1200 --ecoregions
python ortho.py --city Shanghai --provider osm_plain --realms
python ortho.py --city Shanghai --provider osm_plain --realms holt --realm-regions
python ortho.py --lat 0 --lon 20 --provider osm_plain --biomes --realm-regions
```

The samples above are `python ortho.py --config recipes/africa_biomes.toml` and
`python ortho.py --config recipes/madagascar_ecoregions.toml` (`--radius 1200
--ecoregions --biome-alpha 0.7`). The plain map (`osm_plain`) keeps OSM's own
forest and desert greens and yellows from mixing with the biome colours.

**Biomes.** The 14 biomes, with their numbers: 1 Tropical & Subtropical Moist
Broadleaf Forests, 2 Tropical & Subtropical Dry Broadleaf Forests, 3 Tropical &
Subtropical Coniferous Forests, 4 Temperate Broadleaf & Mixed Forests,
5 Temperate Conifer Forests, 6 Boreal Forests/Taiga, 7 Tropical & Subtropical
Grasslands, Savannas & Shrublands, 8 Temperate Grasslands, Savannas &
Shrublands, 9 Flooded Grasslands & Savannas, 10 Montane Grasslands & Shrublands,
11 Tundra, 12 Mediterranean Forests, Woodlands & Scrub, 13 Deserts & Xeric
Shrublands, 14 Mangroves. Ice caps and bare high mountains are a fifteenth class,
Rock & Ice, drawn pale grey (RESOLVE colours it sand, which reads as desert).

**Only some biomes.** `--biome-class` (`biome_classes=` in the API, or the
interactive prompt) shows only the biomes named and turns the layer on by
itself. Give a number, or words that all appear in the names wanted: `forest`
is the seven forest biomes, `temperate` the three temperate ones, `tropical
grass` one, `rock` or `ice` Rock & Ice. Repeat it for several; case is ignored.

**Ecoregions.** `--ecoregions` (`ecoregions=True`) colours each of the 846
ecoregions instead: regions with their own mix of species, such as the
*Madagascar spiny thickets* or the *Iberian sclerophyllous and semi-deciduous
forests*. A whole globe shows a few hundred, so the key names the 12 that cover
most of the map (as drawn, so the foreshortened rim counts for less) and says how
many there are; a radius map shows fewer. With `--biome-class`, only those
biomes' ecoregions are drawn and named. Neighbouring ecoregions sometimes share
a colour in RESOLVE's palette.

**Realms.** `--realms` (`realms="wwf"`) colours the eight biogeographic realms
the ecoregions are grouped into (WWF, Olson et al. 2001): the great regions whose
plants and animals evolved largely apart, after Sclater's and Wallace's
zoogeographic regions. They are the Nearctic (North America), Neotropic (South
and Central America), Palearctic (Europe, North Africa, northern and central
Asia), Afrotropic (Africa south of the Sahara), Indomalayan (South and Southeast
Asia), Australasia (Australia, New Guinea, Wallacea), Oceania (the Pacific
islands) and Antarctica. The key names those on the map and where each lies.
Centred on Shanghai, the map shows the Palearctic–Indomalayan boundary across
southern China and Wallace's Line between Borneo and Sulawesi. Rock & Ice has no
realm and stays clear (the Greenland ice sheet, the high Himalaya). Not with
`--ecoregions`; `--biome-class` keeps only those biomes.

**Holt's zoogeographic realms.** `--realms holt` (`realms="holt"`) colours the
11 realms of [Holt et al. (2013)](https://doi.org/10.1126/science.1228282), the
first full update of Wallace's 1876 map, drawn from the ranges and family trees
of 21,037 species of amphibians, birds and mammals. Beside the classic realms it
separates the Saharo-Arabian, Sino-Japanese, Panamanian, Madagascan and Oceanina
realms. `--realm-regions` outlines its 20 regions (Chinese, Tibetan, Japanese,
Guineo-Congolian, Amazonian …) and names those on the map; it goes over any
layer, so `--biomes --realm-regions` shows the biomes inside each region. Centred
on Shanghai, the city falls in the Sino-Japanese realm's Chinese region, where
WWF has the Palearctic.

- **Terms.** CMEC, which publishes the GIS files, asks that any use of the map
  credit *Science*/AAAS, and gives no other licence. So the files are not shipped
  here: the first run downloads them from
  [CMEC](https://macroecology.ku.dk/resources/wallace) (~18 MB), and every map
  carries the credit (*Zoogeographic realms and regions: Holt et al. (2013),
  Science 339:74-78, credit Science/AAAS*). No sample map is included for the
  same reason. Check with CMEC before publishing maps made with it.
- **Data.** The realms are filled into a 0.05° grid and the region outlines
  kept, under 1 MB together in `~/.cache/ortho_tiles/zoogeography/`. The map stops
  at 56°S (no Antarctica), and its borders follow the coarse grid the analysis
  used, so they run in steps across the continents. Regions are named on their
  largest part in view; Holt's data puts some outlying patches in distant regions
  (part of Queensland in the Novozelandic region), and those can carry the name
  when the main part is out of view.

- **Data.** The first run downloads the RESOLVE shapefile (~150 MB) and fills a
  0.05° (~5.5 km) grid with the ecoregion whose outline holds each cell's centre,
  in about 20 seconds. The grid, under 1 MB, is cached in
  `~/.cache/ortho_tiles/biomes/` and the download deleted. Large lakes are left
  out; ecoregions smaller than a cell can vanish, and maps of under about
  1,500 km show the cells along coasts.
- **Layering.** The biomes are drawn above the vegetation layer and below the
  climate and soil colours; `--biome-alpha` sets their opacity (default 0.6).
- **Licence.** CC BY 4.0: keep the credit line (*Biomes: RESOLVE Ecoregions 2017
  (Dinerstein et al. 2017), CC BY 4.0*) when you share a map.

## Soil Groups

Pass `--soil` (CLI) or `soil=True` (API) to colour the land by its most probable
soil: one of the 30 Reference Soil Groups of the World Reference Base (WRB), the
international soil classification, as mapped by
[SoilGrids 2.0](https://www.isric.org/explore/soilgrids) (ISRIC). A key below the
globe names them.

```powershell
python ortho.py --lat 35 --lon 40 --soil
python ortho.py --lat 50 --lon 60 --soil-class Chernozems --soil-class KS --soil-class Phaeozems
```

The steppe sample above is `python ortho.py --config recipes/steppe_soils.toml`
(those three groups, with `--soil-alpha 0.75`).

**Only some groups.** `--soil-class` (`soil_classes=` in the API, or the
interactive prompt) shows only the groups named and leaves the rest clear; it
turns `--soil` on by itself. Name a group in full, singular or plural, or by its
start (`Chernozem`, `ferral`), or give its two-letter WRB code (`CH`, `FR`).
Repeat it for several; names ignore case. With eight groups or fewer the key
also says what each one is (*Chernozems: black earths, deep humus*).

The groups, with their codes: Acrisols (AC), Albeluvisols (AB), Alisols (AL),
Andosols (AN), Arenosols (AR), Calcisols (CL), Cambisols (CM), Chernozems (CH),
Cryosols (CR), Durisols (DU), Ferralsols (FR), Fluvisols (FL), Gleysols (GL),
Gypsisols (GY), Histosols (HS), Kastanozems (KS), Leptosols (LP), Lixisols (LX),
Luvisols (LV), Nitisols (NT), Phaeozems (PH), Planosols (PL), Plinthosols (PT),
Podzols (PZ), Regosols (RG), Solonchaks (SC), Solonetz (SN), Stagnosols (ST),
Umbrisols (UM), Vertisols (VR).

- **Data.** SoilGrids maps the most probable group for every 250 m cell from
  56°S to 84°N (WRB 2006, CC BY 4.0); the sea, lakes, ice and Antarctica have
  none. ISRIC's web services can return a coarse world grid quickly, but they
  shrink it by averaging the class *numbers*, which is meaningless for
  categories (London's Cambisols and Luvisols average to "Cryosols"). So the
  first `--soil` run downloads the 459 full-resolution files instead, about
  220 MB in a minute or so, and keeps the commonest group in each block of
  32 × 32 cells (0.067°, ~7 km). The result, about 0.5 MB, is cached in
  `~/.cache/ortho_tiles/soil/`; an interrupted download resumes where it stopped.
- **Majority vote.** A block needs at least a third of its cells to be soil to
  get a group, and the commonest group wins, so groups that only occur in small
  patches (Stagnosols, Umbrisols) mostly disappear at this scale.
- **Layering.** The soils are drawn over the climate colours and under crops,
  ice and routes; `--soil-alpha` sets their opacity (default 0.6). Their key
  sits below the climate and elevation keys.

## Soil Properties

Pass `--soil-property` (CLI) or `soil_property=` (API) to map one measured soil
property at a chosen depth, in bands with a key, instead of the soil groups:

```powershell
python ortho.py --lat 25 --lon 20 --soil-property ph
python ortho.py --lat 60 --lon -100 --soil-property organic-carbon --soil-depth 15-30cm
```

| Property | What it is | Bands |
|---|---|---|
| `ph` | Acidity, pH in water | The USDA reaction classes, from < 4.5 (very strongly acid) to ≥ 8.5 (strongly alkaline) |
| `organic-carbon` | Organic carbon, g/kg | < 5 to ≥ 80; peat is far above |
| `clay`, `sand`, `silt` | The fine earth's texture, % | Bands of 10 or 20 points |
| `nitrogen` | Total nitrogen, g/kg | < 0.5 to ≥ 8 |
| `cec` | Cation exchange capacity at pH 7, cmol(c)/kg: how many nutrients the soil can hold | < 5 to ≥ 40 |
| `bulk-density` | Bulk density of the fine earth, g/cm³ | < 1.0 to ≥ 1.6 |
| `coarse-fragments` | Stones and gravel, % by volume | The FAO classes, few (< 5) to dominant (≥ 60) |
| `carbon-stock` | Organic carbon stored in the top 30 cm, kg/m² | < 2 to ≥ 15 |

- **Depths.** `--soil-depth` picks one of SoilGrids' six layers: `0-5cm` (the
  default), `5-15cm`, `15-30cm`, `30-60cm`, `60-100cm` or `100-200cm`.
  `carbon-stock` is mapped for `0-30cm` only.
- **Data.** ISRIC's 5 km versions of the SoilGrids 2.0 predictions (CC BY 4.0):
  one ~4 MB file per property and depth, downloaded the first time it is used,
  moved from its interrupted Goode Homolosine projection to a 0.05° grid, and
  cached in `~/.cache/ortho_tiles/soil/properties/`. Averaging to 5 km suits
  measurements, unlike the soil groups.
- The pH sample above is `python ortho.py --config recipes/topsoil_ph.toml`
  (`--lat 25 --lon 20 --soil-property ph`).
- **One soil layer at a time.** `--soil-property` can't be combined with
  `--soil` or `--soil-class`; `--soil-alpha` sets the opacity of either.

## Crop Areas

`--crop NAME` (`crops=[...]` in the API, or the interactive prompt) shades where
a crop is grown, from [CROPGRIDS](https://doi.org/10.1038/s41597-024-03247-7):
173 crops circa 2020 on a 0.05° (~5.6 km) grid. Each place is shaded by the
share of its land planted with the crop, from clear (under 0.5%) to the full
colour (40% or more). A key below the globe names the crop.

```powershell
python ortho.py --lat 45 --lon 40 --crop wheat
python ortho.py --lat 30 --lon 95 --crop wheat --crop rice --crop maize
python ortho.py --list-crops
```

The two crop samples above were rendered with:

```powershell
python ortho.py --config recipes/london_wheat.toml          # --city london --crop wheat
python ortho.py --config recipes/shanghai_wheat_rice.toml   # --city shanghai --crop wheat --crop rice
```

- **Several crops.** Repeat `--crop`. Each place then takes the colour of
  whichever of the chosen crops covers the most of it, shaded by that crop's
  share. Wheat, rice and maize over Asia show the north–south wheat–rice divide
  in China and India, and the maize belt of north-east China.
- **Names.** `--list-crops` prints all 173. They follow the dataset's file names:
  `sugarcane`, `oilpalm`, `sweetpotato`; "nes" means not elsewhere specified,
  "for" a fodder crop. A misspelt name stops before anything is downloaded, with
  suggestions (`whaet` → "Did you mean wheat?").
- **Colours.** Common crops have their own (wheat gold, rice teal, maize orange,
  coffee brown, …), others get distinct colours in turn. Choose one with
  `NAME:COLOUR`, e.g. `--crop rice:#00a0ff`.
- **Data.** All 173 crops ship in one 807 MB archive, so each crop's file (a few
  MB) is read straight out of it with HTTP range requests and cached in
  `~/.cache/ortho_tiles/cropgrids/`. Reading the files needs `h5py`.
- **Layering.** Crops are drawn over the climate colours and under polar
  ice, routes and areas. Their key sits below the climate key, above the route
  key.
- **Accuracy.** CROPGRIDS rates each cell's data quality; major crops in countries
  with detailed statistics are mapped best, minor crops in data-poor regions are
  more estimated. At globe scale the patterns hold.

## Köppen-Geiger Climate Overlay

Pass `--koppen` (CLI) or `koppen=True` (API) to render a semi-transparent
Köppen-Geiger climate classification layer on top of the globe.

```powershell
python ortho.py --city paris --provider osm --zoom 3 --koppen
python ortho.py --city tokyo --provider google_satellite --zoom 3 --koppen --koppen-alpha 0.6
```

**Data:** the first `--koppen` run downloads the V1 archive
(`Beck_KG_V1.zip`, ~71 MB) from [Figshare](https://doi.org/10.6084/m9.figshare.6396959),
checks its MD5 and extracts the 0.083° present-day raster into
`~/.cache/ortho_tiles/koppen/`. Later runs reuse it. If you already have the
archive, extract it into a `Beck_KG_V1/` folder next to `koppen.py`, which is
checked first. If the data can't be obtained, the map is still rendered without
the overlay and a warning explains what to do.

The overlay uses the 30-class colour scheme from the official dataset and adds
a compact legend strip below the globe.

**Only some classes.** `--koppen-class` (`koppen_classes=` in the API, or the
interactive prompt) shows only the classes named and leaves everything else
clear; it turns `--koppen` on by itself. Give a class (`Cfb`), or the start of
one to take a whole group: `C` is every temperate class, `Cs` the three
Mediterranean ones, `BW` both deserts. Repeat it for several; names ignore case.
With eight classes or fewer the key also names them (*Cfb: Oceanic*). A single
class reads better with a stronger overlay, for example `--koppen-alpha 0.7`.

```powershell
python ortho.py --city london --koppen-class Cfb --koppen-alpha 0.7
python ortho.py --lat 30 --lon 0 --koppen-class Cs --both-hemispheres
```

The Lusaka sample above shows Zambia's dominant climate, Cwa (71% of the
country; then savanna, Aw, 19%; highland Cwb, 6%; hot semi-arid BSh, 3%):
`python ortho.py --config recipes/lusaka_cwa.toml`
(`--city lusaka --koppen-class Cwa --koppen-alpha 0.7`).

The classes are Af, Am, As/Aw (tropical); BWh, BWk, BSh, BSk (arid); Csa, Csb,
Csc, Cwa, Cwb, Cwc, Cfa, Cfb, Cfc (temperate); Dsa–Dsd, Dwa–Dwd, Dfa–Dfd
(continental); ET, EF (polar). The dataset credit
(*Climate data: Beck et al. (2018), CC BY 4.0*) is added to the attribution
block in the bottom-right corner of the image (see below).

## Trewartha Climate Overlay

Pass `--trewartha` (CLI) or `trewartha=True` (API) for the Trewartha
classification instead of Köppen-Geiger. Trewartha redraws the temperate
belts around the length of the growing season (months at 10 °C or more), so
"subtropical" and "temperate" fit how people use the words: London and Paris
are temperate oceanic (Do), Rome and Cape Town subtropical (Cs), Moscow
temperate continental (Dc). Only one climate overlay is drawn at a time, so
`--trewartha` and `--koppen` can't be combined.

```powershell
python ortho.py --city london --trewartha
python ortho.py --city lusaka --trewartha-class C --climate-alpha 0.7
```

The Asia sample above is `python ortho.py --config recipes/himalaya_trewartha.toml`
(`--lat 28 --lon 88 --trewartha`).

**Data:** the classes are computed from [CHELSA v2.1](https://chelsa-climate.org/)
monthly mean temperature and precipitation for 1981–2010 (public domain, CC0).
The CHELSA files are 1 km Cloud-Optimised GeoTIFFs with smaller copies inside,
so the first run reads only the ~15 km copy (0.133°) of each of the 24 monthly
files, about 52 MB, with HTTP range requests. The highland group also needs
heights: 256 terrain tiles at zoom 4 (~10 km, about 25 MB; see "Elevation").
That takes a minute or so; the classified grid is then cached in
`~/.cache/ortho_tiles/trewartha/` (about 100 KB) and later runs reuse it. CHELSA covers the oceans too, so they
are cleared with the Natural Earth land outline. If the data can't be obtained,
the map is still rendered without the overlay and a warning explains why.

**Rules** (Belda et al. 2014, with Patton's dry threshold). Winter is
October–March in the north and April–September in the south.

| Group | Test, in this order | Classes |
|---|---|---|
| B, dry | Annual precipitation (cm) < 2.3 T − 0.64 Pw + 41, with T the mean annual temperature (°C) and Pw the share of precipitation falling in winter (%) | BW desert (below half the threshold), BS steppe |
| A, tropical | Every month ≥ 18 °C | Ar wet (at most 2 months under 60 mm), Aw dry winter, As dry summer |
| C, subtropical | 8–12 months ≥ 10 °C | Cs dry summer, Cw dry winter (both: under 890 mm a year, driest month of that season under 30 mm and under a third of the other season's wettest), Cf humid |
| D, temperate | 4–7 months ≥ 10 °C | Do oceanic (coldest month above 0 °C), Dc continental |
| E, boreal | 1–3 months ≥ 10 °C | Eo oceanic (coldest month above −10 °C), Ec continental |
| F, polar | No month ≥ 10 °C | Ft tundra (warmest month above 0 °C), Fi ice cap |
| H, highland | Ground at least 1,500 m high whose group would change without its height above 1,500 m (see below) | H, in place of the class above |

**Only some classes.** `--trewartha-class` (`trewartha_classes=` in the API,
or the interactive prompt) works like `--koppen-class`: give a class (`Do`) or
a group (`C`), repeat it for several, and it turns `--trewartha` on by itself.
`--climate-alpha` sets the opacity of either overlay.

**Highlands (H).** Trewartha puts high mountains and plateaus, whose climate
is set by their altitude rather than their latitude, in a group of their own,
but never gave it a numerical test, and the published Trewartha maps this
overlay follows (Belda et al. 2014, Huang et al. 2023) leave it out. Here a
cell is H when its ground is at least 1,500 m high **and** taking away its
height above 1,500 m would put it in another group. Taking away height warms
every month by 6.5 °C per km, the standard atmosphere's lapse rate. Heights
come from the terrain tiles at ~10 km.

The test lowers each cell to 1,500 m, not to sea level: 1,500 m alone is
worth almost 10 °C, so lowering everything to the sea would make nearly all
high ground H, including cities with the climate of the lowlands around them.
Some places, with the class they would otherwise get:

| Place | Height | Without H | With H | Group at 1,500 m |
|---|---|---|---|---|
| Tibetan Plateau (33°N 88°E) | 5,175 m | Ft | **H** | BW |
| Lhasa | 3,767 m | Dc | **H** | BS |
| La Paz | 4,205 m | Ft | **H** | Aw |
| Quito | 2,505 m | Cf | **H** | Ar |
| Bogotá | 2,809 m | Cf | **H** | As |
| Addis Ababa | 2,202 m | Cf | **H** | Aw |
| Mexico City | 2,246 m | Cw | **H** | BS |
| High Alps (Jungfrau) | 2,473 m | Eo | **H** | Dc |
| Denver | 1,582 m | Dc | Dc | Dc |
| Johannesburg | 1,662 m | Cw | Cw | Cw |
| Nairobi | 1,620 m | Cw | Cw | Cw |
| Isfahan | 1,714 m | BS | BS | BS |
| Greenland ice sheet | 3,207 m | Fi | Fi | Fi |

Heights are the ~10 km cell's, so they differ from the cities' own. Lowered
to sea level instead, Denver, Johannesburg, Nairobi and Kunming would all be
H too. Ice caps stay F: lowered to 1,500 m, Greenland's summit would still be
an ice cap (Fi).

**Caveats.**
- Where H starts is a choice, not a law: a cell just above 1,500 m whose class
  sits near a boundary can flip to H, while a high cell whose group survives
  the lowering keeps its own class (Isfahan, Sanaa).
- At ~15 km a cell averages its terrain, and some places sit right on a
  boundary: Denver comes out Dc, 434 mm of rain against a dry threshold of
  419 mm, while the plains to its east are BS.
- Published Trewartha maps differ in details (the C dry-season rules, the dry
  threshold), so borders can shift a little against other maps.

The credits (*Climate data: CHELSA v2.1 (Karger et al. 2017), CC0; Trewartha
classes computed*, and the elevation credit for the heights behind H) are added
to the attribution block.

## Data Sources & Licensing

| Data | Authors | License | Reference |
|---|---|---|---|
| Köppen-Geiger climate classification V1, present day (1980–2016), used at 0.083° (~10 km) | Beck, H. E. et al. (2018) | [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) | [doi:10.1038/sdata.2018.214](https://doi.org/10.1038/sdata.2018.214) |
| CHELSA v2.1 monthly mean temperature, precipitation and relative humidity, 1981–2010, used at 0.133° (~15 km) for the climate means and the Trewartha classes | Karger, D. N. et al. (2017) | [CC0 1.0](https://creativecommons.org/publicdomain/zero/1.0/) | [doi:10.1038/sdata.2017.122](https://doi.org/10.1038/sdata.2017.122), [chelsa-climate.org](https://chelsa-climate.org/) |
| MODIS Land Cover Type (MCD12Q1 v6.1, IGBP), yearly 2001–2024, 500 m | Friedl, M. & Sulla-Menashe, D., NASA LP DAAC | NASA open data, no restrictions | [doi:10.5067/MODIS/MCD12Q1.061](https://doi.org/10.5067/MODIS/MCD12Q1.061), via [NASA GIBS](https://www.earthdata.nasa.gov/engage/open-data-services-software/earthdata-developer-portal/gibs-api) |
| MODIS Terra Vegetation Indices monthly (NDVI, MOD13A3 v6.1, 1 km) | Didan, K., NASA LP DAAC | NASA open data, no restrictions | [doi:10.5067/MODIS/MOD13A3.061](https://doi.org/10.5067/MODIS/MOD13A3.061), via NASA GIBS |
| Mapzen Terrain Tiles (Terrarium), from GMTED2010 and SRTM (USGS) and ETOPO1 (NOAA), used at zoom 4–5 for the elevation layer and the Trewartha highlands | Mapzen / Tilezen, AWS Open Data | Free to use; the sources must be credited ([attribution](https://github.com/tilezen/joerd/blob/master/docs/attribution.md)) | [registry.opendata.aws/terrain-tiles](https://registry.opendata.aws/terrain-tiles/) |
| RESOLVE Ecoregions 2017: 846 ecoregions in 14 biomes and 8 realms, used at 0.05° (~5.5 km) | Dinerstein, E. et al. (2017), RESOLVE | [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) | [doi:10.1093/biosci/bix014](https://doi.org/10.1093/biosci/bix014), [ecoregions.appspot.com](https://ecoregions.appspot.com/) |
| Zoogeographic realms (11) and regions (20), from 21,037 species of amphibians, birds and mammals; realms used at 0.05° | Holt, B. G. et al. (2013); GIS files from CMEC, University of Copenhagen | No licence given; any use must credit *Science*/AAAS. Downloaded by each user, not shipped | [doi:10.1126/science.1228282](https://doi.org/10.1126/science.1228282), [macroecology.ku.dk/resources/wallace](https://macroecology.ku.dk/resources/wallace) |
| SoilGrids 2.0: most probable WRB Reference Soil Group, 250 m, used at 0.067° (~7 km); soil properties, 5 km aggregates, used at 0.05° | Poggio, L. et al. (2021), ISRIC World Soil Information | [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) | [doi:10.5194/soil-7-217-2021](https://doi.org/10.5194/soil-7-217-2021), [isric.org/explore/soilgrids](https://www.isric.org/explore/soilgrids) |
| NCEP/NCAR Reanalysis 1, long-term monthly means 1991–2020 of the 10 m wind (u, v) and wind speed, and 1981–2010 of the 2 m air temperature (for `--temperature-over-sea`), T62 Gaussian grid (~1.9°) | Kalnay, E. et al. (1996); NOAA PSL | Public domain (US government); acknowledgement requested: "NCEP-NCAR Reanalysis 1 data provided by the NOAA PSL, Boulder, Colorado, USA" | [doi:10.1175/1520-0477(1996)077<0437:TNYRP>2.0.CO;2](https://doi.org/10.1175/1520-0477(1996)077%3C0437:TNYRP%3E2.0.CO;2), [psl.noaa.gov](https://psl.noaa.gov/data/gridded/data.ncep.reanalysis.derived.html) |
| NOAA OI SST V2.1 High Resolution, long-term monthly means 1991–2020 of the sea surface temperature, 0.25° | Huang, B. et al. (2021); NOAA NCEI, via NOAA PSL | Public domain (US government); acknowledgement requested: "NOAA OI SST V2 High Resolution data provided by the NOAA PSL, Boulder, Colorado, USA" | [doi:10.1175/JCLI-D-20-0166.1](https://doi.org/10.1175/JCLI-D-20-0166.1), [psl.noaa.gov](https://psl.noaa.gov/data/gridded/data.noaa.oisst.v2.highres.html) |
| Natural Earth (land / ocean fallback, Trewartha, climate mean and elevation land masks, polar land ice) | Natural Earth contributors | Public domain | [naturalearthdata.com](https://www.naturalearthdata.com/) |
| PaleoMIST 1.0: ice sheet margins 20,000 years ago | Gowan, E. J. et al. (2021) | [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) | [doi:10.1038/s41467-021-21469-w](https://doi.org/10.1038/s41467-021-21469-w), data [doi:10.1594/PANGAEA.905800](https://doi.org/10.1594/PANGAEA.905800) |
| PB2002 plate boundaries, converted by Hugo Ahlenius (Nordpil) | Bird, P. (2003) | [ODC-By 1.0](https://opendatacommons.org/licenses/by/1-0/) | [doi:10.1029/2001GC000252](https://doi.org/10.1029/2001GC000252), [github.com/fraxen/tectonicplates](https://github.com/fraxen/tectonicplates) |
| ANSS Comprehensive Earthquake Catalog (ComCat), 1900 on | U.S. Geological Survey | Public domain (US government) | [earthquake.usgs.gov](https://earthquake.usgs.gov/fdsnws/event/1/) |
| Volcanoes of the World v5, Holocene volcanoes | Global Volcanism Program (2025), Smithsonian Institution | Free to use with citation | [doi:10.5479/si.GVP.VOTW5-2025.5.3](https://doi.org/10.5479/si.GVP.VOTW5-2025.5.3) |
| Global Drifter Program near-surface velocity climatology, annual means at 0.25°, used at 1° | Laurindo, L. C. et al. (2017), NOAA AOML | Public domain (US government) | [doi:10.1016/j.dsr.2017.04.009](https://doi.org/10.1016/j.dsr.2017.04.009) |
| HydroBASINS level 4 (HydroSHEDS) | Lehner, B. & Grill, G. (2013), WWF | Free for scientific, educational and commercial use, with citation ([licence](https://www.hydrosheds.org/products/hydrobasins)) | [doi:10.1002/hyp.9740](https://doi.org/10.1002/hyp.9740) |
| Natural Earth 1:10m rivers and time zones | Natural Earth contributors | Public domain | [naturalearthdata.com](https://www.naturalearthdata.com/) |
| IBTrACS v04r01, tropical cyclone tracks since 1980 | Knapp, K. R. et al. (2010), NOAA NCEI | Public domain (US government) | [doi:10.1175/2009BAMS2755.1](https://doi.org/10.1175/2009BAMS2755.1) |
| Northern Hemisphere permafrost zones, 2000–2016, 1 km, used at 5 km | Obu, J. et al. (2019), ESA Permafrost CCI / University of Oslo | [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) | [doi:10.1016/j.earscirev.2019.04.023](https://doi.org/10.1016/j.earscirev.2019.04.023), data on [PANGAEA](https://doi.org/10.1594/PANGAEA.888600) |
| Gridded Population of the World v4.11, population density 2000–2020, via NASA GIBS | CIESIN (2018), NASA SEDAC | [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) | [doi:10.7927/H49C6VHW](https://doi.org/10.7927/H49C6VHW) |
| Gridded Livestock of the World v4, 2015, 5 arc-minutes | Gilbert, M. et al., FAO | [CC0 1.0](https://creativecommons.org/publicdomain/zero/1.0/) | Harvard Dataverse, e.g. cattle [doi:10.7910/DVN/LHBICE](https://doi.org/10.7910/DVN/LHBICE) |
| Malaria Atlas Project, 2026 release: *P. falciparum* (PfPR2–10) and *P. vivax* parasite rates, 2000–2025, 5 km, used at ~10 km | Malaria Atlas Project, The Kids Research Institute Australia | [CC BY 3.0](https://creativecommons.org/licenses/by/3.0/) ([open access policy](https://malariaatlas.org/open-access-policy/)) | [malariaatlas.org](https://malariaatlas.org/), WCS at data.malariaatlas.org |
| Global compendium of *Aedes aegypti* and *Ae. albopictus* occurrence, 1958–2014 | Kraemer, M. U. G. et al. (2015) | [CC0 1.0](https://creativecommons.org/publicdomain/zero/1.0/) | [doi:10.1038/sdata.2015.35](https://doi.org/10.1038/sdata.2015.35), data [doi:10.5061/dryad.47v3c](https://doi.org/10.5061/dryad.47v3c), read from GBIF |
| Global Shipping Traffic Density: hourly AIS positions of ships, January 2015 – February 2021, 0.005° | World Bank, from the IMF's World Seaborne Trade Monitoring System | [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) | [datacatalog.worldbank.org](https://datacatalog.worldbank.org/search/dataset/0037580) |
| PortWatch ports database: 2,065 ports with the ships calling a year by kind, 2019–2025 average, from AIS | International Monetary Fund (PortWatch, with the University of Oxford) | IMF data, free reuse with attribution ([terms](https://www.imf.org/external/terms.htm), Special Terms for Statistical Data) | [portwatch.imf.org](https://portwatch.imf.org/datasets/acc668d199d1472abaaf2467133d4ca4) |
| OpenFlights airport, airline and route databases (routes as of June 2014) | OpenFlights.org | [ODbL 1.0](https://opendatacommons.org/licenses/odbl/1-0/) | [openflights.org/data](https://openflights.org/data), [GitHub](https://github.com/jpatokal/openflights) |
| Climate TRACE emissions inventory: individual plants with location, type and yearly emissions | Climate TRACE coalition | [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) ([terms](https://climatetrace.org/terms)) | [climatetrace.org](https://climatetrace.org/data), [API](https://api.climatetrace.org/v6/swagger/index.html) |
| World Development Indicators, read from the World Bank's API | World Bank | [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) | [data.worldbank.org/indicator](https://data.worldbank.org/indicator), [API](https://datahelpdesk.worldbank.org/knowledgebase/articles/889392) |
| Natural Earth 1:50m countries (for `--indicator`) | Natural Earth contributors | Public domain | [naturalearthdata.com](https://www.naturalearthdata.com/) |
| Eurostat regional statistics, read from Eurostat's API | Eurostat | Reuse allowed with acknowledgement ([Eurostat copyright notice](https://ec.europa.eu/eurostat/about-us/policies/copyright)) | [Eurostat database](https://ec.europa.eu/eurostat/data/database), [API](https://wikis.ec.europa.eu/display/EUROSTATHELP/API+Statistics+-+data+query) |
| NUTS regions (2024, 2021, 2016) at 1:10 million, from GISCO | Eurostat GISCO; © EuroGeographics for the administrative boundaries | Free with attribution; see [GISCO's terms](https://ec.europa.eu/eurostat/web/gisco/geodata/administrative-units) | [GISCO NUTS](https://ec.europa.eu/eurostat/web/gisco/geodata/statistical-units/territorial-units-statistics) |
| GeoNames places of 5,000 people and more (cities5000), used to restore the urban centres' names | GeoNames | [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) | [geonames.org](https://www.geonames.org), [download.geonames.org/export/dump](https://download.geonames.org/export/dump/) |
| GHS Urban Centre Database 2025 (R2024A, v1.2), multi-temporal urban centres 1975–2030 | European Commission, Joint Research Centre (Mari Rivero, Melchiorri et al. 2024) | [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) | [doi:10.2905/1a338be6-7eaf-480c-9664-3a8ade88cbcd](https://doi.org/10.2905/1a338be6-7eaf-480c-9664-3a8ade88cbcd), [human-settlement.emergency.copernicus.eu](https://human-settlement.emergency.copernicus.eu/ghs_ucdb_2024.php) |
| BirdLife bird species distribution maps (GeoPackage) and the HBW-BirdLife checklist, read from files each user requests | BirdLife International and Handbook of the Birds of the World | [BirdLife data terms of use](https://datazone.birdlife.org/about-our-science/terms-of-use): non-commercial use; no reposting or redistribution, of the data or of works made from it, without BirdLife's permission. Not shipped or downloaded here | [datazone.birdlife.org/species/requestdis](https://datazone.birdlife.org/species/requestdis) |
| IUCN Red List range polygons (spatial data), read from files each user downloads | IUCN | [IUCN Red List Terms and Conditions of Use](https://www.iucnredlist.org/terms/terms-of-use): non-commercial use; no reposting or redistribution, of the data or of works made from it, without IUCN's permission. Not shipped or downloaded here | [iucnredlist.org/resources/spatial-data-download](https://www.iucnredlist.org/resources/spatial-data-download) |
| GBIF occurrence records, read as occurrence density maps (zoom 1, ~20 km cells) | GBIF.org and its data publishers | Per dataset: [CC0 1.0](https://creativecommons.org/publicdomain/zero/1.0/), [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) or [CC BY-NC 4.0](https://creativecommons.org/licenses/by-nc/4.0/); see [GBIF's terms](https://www.gbif.org/terms) | [gbif.org](https://www.gbif.org/), [maps API](https://techdocs.gbif.org/en/openapi/v2/maps) |
| Cliopatria v0.2.1: borders of over 1,600 states and empires, 3400 BCE–2024 | Bennett, J. S. et al. (2025), Seshat Global History Databank | [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) | [doi:10.1038/s41597-025-04516-9](https://doi.org/10.1038/s41597-025-04516-9), [github.com/Seshat-Global-History-Databank/cliopatria](https://github.com/Seshat-Global-History-Databank/cliopatria) |
| CShapes 2.0: borders of states and dependent territories, 1886–2019 (Gleditsch & Ward system), from the `cshapes` R package | Schvitz, G. et al. (2022), ETH Zurich | No data licence stated; the article is CC BY-NC 4.0 and the R package GPL ≥ 2. Downloaded by each user, not shipped | [doi:10.1177/00220027211013563](https://doi.org/10.1177/00220027211013563), [icr.ethz.ch/data/cshapes](https://icr.ethz.ch/data/cshapes/) |
| CROPGRIDS v1.08, physical crop area of 173 crops, c. 2020, 0.05° | Tang, F. H. M. et al. (2024) | [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) | [doi:10.1038/s41597-024-03247-7](https://doi.org/10.1038/s41597-024-03247-7), data [doi:10.6084/m9.figshare.22491997](https://doi.org/10.6084/m9.figshare.22491997) |
| Sea Ice Index, Version 4 (G02135), monthly sea ice extent | Fetterer, F. et al. (2025), NSIDC | Free to use; citation required | [doi:10.7265/a98x-0f50](https://doi.org/10.7265/a98x-0f50) |
| OpenStreetMap tiles | OpenStreetMap contributors | Data [ODbL](https://www.openstreetmap.org/copyright); attribution required by the [tile usage policy](https://operations.osmfoundation.org/policies/tiles/) | [openstreetmap.org](https://www.openstreetmap.org/) |
| NASA Black Marble 2016 (VIIRS day/night band night lights, layer `VIIRS_Black_Marble`) via GIBS | NASA Earth Observatory; NASA ESDIS | Public domain (NASA open data, CC0); acknowledgement requested | [NASA GIBS](https://www.earthdata.nasa.gov/engage/open-data-services-software/earthdata-developer-portal/gibs-api) |
| NASA Blue Marble (Next Generation, shaded relief and bathymetry) via GIBS | NASA Earth Observatory; NASA ESDIS | Public domain (NASA open data, CC0); acknowledgement requested | [NASA GIBS](https://www.earthdata.nasa.gov/engage/open-data-services-software/earthdata-developer-portal/gibs-api) |
| Google map / satellite tiles (Map Tiles API, API key required) | Google | Proprietary, [Google Maps terms](https://cloud.google.com/maps-platform/terms) | [google.com/maps](https://www.google.com/maps) |

Every render carries an attribution block in its bottom-right corner, outside
the globe:

| Layer | Credit line |
|---|---|
| `osm`, `osm_plain` tiles | *Map tiles © OpenStreetMap contributors* |
| `nasa` tiles | *Imagery: NASA Blue Marble, via NASA GIBS (ESDIS)* |
| `nasa_night` tiles | *Imagery: NASA Black Marble 2016 (VIIRS night lights), via NASA GIBS (ESDIS)* |
| `google`, `google_satellite` tiles | *Google Maps* plus the copyright string returned by the Map Tiles API |
| `--koppen` overlay | *Climate data: Beck et al. (2018), CC BY 4.0* |
| `--trewartha` overlay | *Climate data: CHELSA v2.1 (Karger et al. 2017), CC0; Trewartha classes computed*, plus the elevation credit |
| `--temperature`, `--precipitation`, `--humidity` layers | *Climate means: CHELSA v2.1, 1981–2010 (Karger et al. 2017), CC0* |
| `--temperature-over-sea` | *Air temperature over the sea: NCEP/NCAR Reanalysis 1, 1981–2010 means, NOAA PSL* |
| `--sea-temperature` layer | *Sea surface temperature: NOAA OISST v2.1, 1991–2020 means, NOAA PSL* |
| `--elevation` layer | *Elevation: Mapzen Terrain Tiles (AWS Open Data); GMTED2010 and SRTM courtesy of the USGS; ETOPO1, NOAA NCEI* |
| `--landcover` layer | *Land cover: MODIS MCD12Q1 IGBP YYYY (NASA LP DAAC), via NASA GIBS* |
| `--ndvi` layer | *NDVI: MODIS Terra monthly, Month YYYY (NASA), via NASA GIBS* |
| `--biomes` layer | *Biomes: RESOLVE Ecoregions 2017 (Dinerstein et al. 2017), CC BY 4.0* |
| `--ecoregions` layer | *Ecoregions: RESOLVE Ecoregions 2017 (Dinerstein et al. 2017), CC BY 4.0* |
| `--realms` (`wwf`) layer | *Realms: RESOLVE Ecoregions 2017 (Dinerstein et al. 2017), CC BY 4.0* |
| `--realms holt`, `--realm-regions` | *Zoogeographic realms and regions: Holt et al. (2013), Science 339:74-78, credit Science/AAAS* |
| `--soil` overlay | *Soil groups: SoilGrids 2.0, ISRIC (Poggio et al. 2021), CC BY 4.0* |
| `--soil-property` overlay | *Soil properties: SoilGrids 2.0, ISRIC (Poggio et al. 2021), CC BY 4.0* |
| `--wind` layer | *Winds: NCEP/NCAR Reanalysis 1, 1991–2020 means, NOAA PSL* |
| `--ice` overlay | *Sea ice: NSIDC Sea Ice Index v4, extent March YYYY (Arctic) and September YYYY (Antarctic)* |
| `--borders` (Cliopatria) | *Borders: Cliopatria v0.2.1, Seshat Global History Databank (Bennett et al. 2025), CC BY 4.0* |
| `--borders` (CShapes) | *Borders: CShapes 2.0 (Schvitz et al. 2022), ETH Zurich* |
| `--ocean-depth`, `--ice-age` | the elevation credit above |
| `--ice-age` (ice sheets) | *Ice sheets 20,000 years ago: PaleoMIST 1.0 (Gowan et al. 2021), PANGAEA, CC BY 4.0* |
| `--plates` | *Plate boundaries: Bird (2003), PB2002, converted by Ahlenius (Nordpil), ODC-By* |
| `--earthquakes` | *Earthquakes: USGS ANSS Comprehensive Catalog (ComCat)* |
| `--volcanoes` | *Volcanoes: Global Volcanism Program, Volcanoes of the World v5, Smithsonian Institution* |
| `--currents` | *Ocean currents: NOAA Global Drifter Program climatology (Laurindo et al. 2017), NOAA AOML* |
| `--rivers` | *Rivers: Natural Earth* |
| `--basins` | *River basins: HydroBASINS (Lehner & Grill 2013), HydroSHEDS / WWF* |
| `--cyclones` | *Cyclone tracks: IBTrACS v04r01 (Knapp et al. 2010), NOAA NCEI* |
| `--time-zones` | *Time zones: Natural Earth* |
| `--permafrost` | *Permafrost: Obu et al. (2019), ESA Permafrost CCI / University of Oslo, CC BY 4.0* |
| `--population` | *Population density: GPWv4.11 YEAR (CIESIN 2018), NASA SEDAC, via NASA GIBS, CC BY 4.0* |
| `--livestock` | *Livestock: Gridded Livestock of the World v4, 2015 (FAO), CC0* |
| `--malaria` | *Malaria: Malaria Atlas Project (2026 release), CC BY 3.0* |
| `--aedes` | *Aedes records: Kraemer et al. (2015), global compendium of Aedes occurrence, via GBIF, CC0* |
| `--species` | *Species records: GBIF.org occurrence data, retrieved YYYY-MM-DD* |
| `--cities` | *Urban centres: GHS-UCDB R2024A (European Commission, JRC), CC BY 4.0; names restored from GeoNames, CC BY 4.0* |
| `--industry` | *Industry: Climate TRACE (climatetrace.org), CC BY 4.0, retrieved YYYY-MM-DD* |
| `--shipping` | *Shipping: Global Shipping Traffic Density (World Bank, IMF, AIS 2015-2021), CC BY 4.0* |
| `--ports` | *Ports: IMF PortWatch (portwatch.imf.org), ships calling 2019-2025* |
| `--air-routes` | *Air routes: OpenFlights (openflights.org), June 2014, ODbL* |
| `--indicator` | *Statistics: World Bank, World Development Indicators (CODE), CC BY 4.0, retrieved YYYY-MM-DD; countries: Natural Earth*, or *Statistics: Eurostat (DATASET), retrieved YYYY-MM-DD; regions: © EuroGeographics for the administrative boundaries* |
| `--iucn` | *Range: IUCN (YYYY), The IUCN Red List of Threatened Species, spatial data of YYYY-MM-DD*, or for birds *Range: BirdLife International and Handbook of the Birds of the World (YYYY), Bird species distribution maps of the world, version YYYY.N* |
| `--crop` overlay | *Crop areas: CROPGRIDS v1.08, Tang et al. (2024), CC BY 4.0* |

Keep this block (or credit the same sources elsewhere) when you share or publish
a map; the OSM tile policy, the CC BY 4.0 licences and NSIDC's data use terms all require it.

### Google tiles (API key required)

The `google` and `google_satellite` providers use the official
[Map Tiles API](https://developers.google.com/maps/documentation/tile/2d-tiles-overview).
Enable the Map Tiles API (with billing) in a Google Cloud project and create an
API key whose API restrictions allow the Map Tiles API.

The key is read from `GOOGLE_MAPS_API_KEY`, or from `GOOGLE_API_KEY` if that is
not set. The CLI loads keys from a central file before reading the
environment, without overriding variables that are already set:

1. `$ORTHO_ENV_FILE` (explicit override)
2. `~/myapikeys.env` (central key file)
3. `./.env`

So the simplest setup is one line in `~/myapikeys.env`:

```text
GOOGLE_MAPS_API_KEY=your-key
```

```powershell
python ortho.py --city tokyo --provider google_satellite
```

A Gemini key from AI Studio (`GOOGLE_API_KEY`) is usually restricted to the
Gemini API, and Google answers with HTTP 403 "Requests to this API … are
blocked". Use a separate Maps key in `GOOGLE_MAPS_API_KEY`, or add the Map Tiles
API to that key's allowed APIs.

**Satellite tiles and EEA billing.** Since 8 July 2025, Google no longer serves
satellite tiles (`google_satellite`) through the Map Tiles API to projects whose
billing account is in the European Economic Area: projects created after that
date, and older ones once they are modified. Such a project gets HTTP 403
"satellite tiles and 3D tiles are not available for your account and region",
while the `google` road map keeps working. Google's suggested replacements are
the Maps JavaScript API and the mobile SDKs, which this tool cannot use; see
[Map Tiles API restrictions for EEA customers](https://developers.google.com/maps/comms/eea/map-tiles).
The Google satellite samples in this README were rendered before the restriction
reached this project's key. For satellite imagery without Google, use
`--provider nasa`, which is free and has no such limits.

Interactive mode asks for the key (input hidden) if neither variable is set.
Google renders are credited with "Google Maps" plus the copyright string the
API returns for the map. Google's policies also restrict caching and some uses
of exported imagery; check the Map Tiles API policies before publishing.

## Project Files

- [ortho.py](ortho.py): main script and reusable map-generation functions
- [koppen.py](koppen.py): Köppen-Geiger climate overlay and legend
- [trewartha.py](trewartha.py): Trewartha climate classes computed from CHELSA v2.1, overlay and legend
- [elevation.py](elevation.py): elevation layer and heights from the Mapzen terrain tiles
- [vegetation.py](vegetation.py): MODIS land cover and NDVI layers and keys (NASA GIBS tiles)
- [biomes.py](biomes.py): biome, ecoregion and realm layer and keys (RESOLVE Ecoregions 2017, rasterised from its shapefile)
- [iceage.py](iceage.py): the ice sheets of 20,000 years ago (PaleoMIST), read from a remote archive by range requests
- [tectonics.py](tectonics.py): plate boundaries, earthquakes and volcanoes, and their key
- [currents.py](currents.py): ocean surface current arrows, warm and cold, and their key
- [hydrology.py](hydrology.py): rivers, and river basins merged from HydroBASINS and named
- [cyclones.py](cyclones.py): tropical cyclone tracks by Saffir-Simpson category
- [timezones.py](timezones.py): standard-time zones, shaded and labelled
- [permafrost.py](permafrost.py): Northern Hemisphere permafrost zones
- [population.py](population.py): population density from GPWv4 tiles (NASA GIBS), in classed bands
- [livestock.py](livestock.py): livestock density from Gridded Livestock of the World
- [malaria.py](malaria.py): the malaria parasite rate from the Malaria Atlas Project, in classed bands
- [aedes.py](aedes.py): where the dengue mosquitoes have been recorded, from Kraemer et al.'s compendium on GBIF
- [gbif.py](gbif.py): where any species has been recorded, from GBIF's occurrence density maps, with name lookup
- [iucn.py](iucn.py): a species' range from the IUCN Red List or, for birds, BirdLife, read from the files the user downloaded
- [cities.py](cities.py): big cities, the GHSL urban centres with their names (restored from GeoNames), outlines and populations from 1975 to 2030
- [indicators.py](indicators.py): statistics by country (World Bank) or European region (Eurostat), in classes, with the country and NUTS region shapes
- [industry.py](industry.py): industrial plants worldwide from Climate TRACE, as circles sized by emissions
- [shipping.py](shipping.py): ship traffic from the World Bank and IMF's AIS density grids, read once into cached tiles
- [ports.py](ports.py): the IMF PortWatch ports as dots sized by the ships calling, the busiest named
- [air_routes.py](air_routes.py): the OpenFlights airline network as great-circle arcs
- [borders.py](borders.py): historical borders from Cliopatria and CShapes 2.0: dates, colours apart from neighbours, names and key
- [zoogeography.py](zoogeography.py): Holt et al.'s zoogeographic realms (filled) and regions (outlined and named), and their key
- [soil.py](soil.py): soil group overlay and key (SoilGrids 2.0, reduced by majority vote)
- [soil_properties.py](soil_properties.py): soil property overlay and key (SoilGrids 2.0, 5 km)
- [wind.py](wind.py): prevailing-wind arrows and key (NCEP/NCAR Reanalysis 1991–2020 means)
- [recipe_store.py](recipe_store.py): recipes and route files for the web app: read into the form, written back, uploaded
- [climate.py](climate.py): mean temperature, precipitation and humidity layers and key (CHELSA v2.1, 1981–2010)
- [sea.py](sea.py): temperatures over the sea, the surface's (NOAA OISST v2.1) and the air's (NCEP/NCAR Reanalysis 1)
- [ice.py](ice.py): polar ice overlay (NSIDC sea ice extent, Natural Earth land ice)
- [crops.py](crops.py): crop-area overlay and key (CROPGRIDS, read from the remote archive)
- [rotation.py](rotation.py): orthographic globes with any compass direction at the top
- [routes.py](routes.py): GeoJSON route and area loading, drawing and the route key
- [google_tiles.py](google_tiles.py): Google Map Tiles API client (API key, sessions, attribution)
- [tile_fetch.py](tile_fetch.py): single-tile downloader shared by the tile sources
- [webapp.py](webapp.py): the local web app (FastAPI): map requests, the render queue and the JSON API
- [atlas.py](atlas.py): atlases: the atlas file, checking and drawing its maps, laying out the PDF, and the files for the web app
- [web/](web/): the web app's page, script and styles
- [LICENSE](LICENSE): MIT licence
- [routes/](routes/): bundled route files
- [recipes/](recipes/): recipe files for the sample maps (`--config`)
- [atlases/](atlases/): atlas files (`atlas.py`): the Romanian school atlas
- [requirements.txt](requirements.txt): minimum dependency versions
- [pyproject.toml](pyproject.toml): project metadata and the `ortho`, `ortho-web` and `ortho-atlas` commands
- [tests/](tests/): unit test suite

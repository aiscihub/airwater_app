# Source Attribution and Notes

The water-uptake model is trained on real water-adsorption isotherms pulled from the NIST Isotherm Database (see `data/provenance_manifest.json` for the full per-material audit trail). Climate profiles, device-economics assumptions, and the reference material list draw on the public research and data resources below:

- AIYES competition page: Track 1 asks for a slide presentation, video walkthrough/live demo, and source code or deployment link. https://www.aiyes.org/competition
- NASA POWER Hourly API: hourly solar and meteorological data suitable for applications. https://power.larc.nasa.gov/docs/services/api/temporal/hourly/
- NIST Data Resources for Adsorption Science and Technology: adsorption isotherms and adsorbent-material metadata. https://www.nist.gov/programs-projects/nist-data-resources-adsorption
- ACS Sustainable Chemistry and Engineering 2023 article: machine-learning prediction and screening of MOFs for water harvesting. https://pubs.acs.org/doi/abs/10.1021/acssuschemeng.3c01233
- MOF-801 water-harvesting work: Science 2017, DOI 10.1126/science.aam8743.
- MOF-303 rapid cycling work: ACS Central Science 2019, DOI 10.1021/acscentsci.9b00745.
- WHO/UNICEF 2025 WASH update: global safely managed drinking-water access. https://www.who.int/news/item/26-08-2025-1-in-4-people-globally-still-lack-access-to-safe-drinking-water---who--unicef

The app labels all numerical outputs as simulated planning estimates, not guarantees. Regeneration temperature, cycle time, water-stability score, cost score, pore volume, and surface area are not derivable from adsorption isotherms and remain literature-informed estimates; Tier C materials (MOF-303, MOF-801, MOF-841) have no NIST ISODB water isotherm and keep original literature-derived descriptors, excluded from model training. See the "Model and data limitations" section of `README.md` for the full accounting.

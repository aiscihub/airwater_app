# AirWater AI - Competition Submission Draft

## Project title

AirWater AI: Climate-Smart MOF Selection for Atmospheric Water Harvesting

## Track

Track 1: AI Application Development

## One-sentence pitch

AirWater AI is a functional browser application that combines local climate conditions, MOF water-uptake prediction, explainable ranking, and schedule optimization to prioritize materials for atmospheric water-harvesting experiments.

## Problem

Atmospheric water harvesting using metal-organic frameworks is promising, but selecting a MOF is difficult. Maximum laboratory water uptake does not tell a user whether the material will work in a real location. Real performance depends on humidity, temperature, solar availability, regeneration temperature, cycle time, water stability, evidence quality, and device losses.

Students, researchers, and early-stage engineers need a way to translate climate conditions into a defensible material-screening decision before spending time and money on laboratory testing.

## Functional solution

AirWater AI lets the user choose a location, season, water target, MOF mass, energy source, regeneration-temperature limit, and device-efficiency assumption. The application then:

1. Builds a 24-hour climate profile.
2. Predicts uptake for each candidate material.
3. Calculates working capacity and simulated daily water yield.
4. Ranks candidates using climate fit, heat feasibility, evidence, stability, cost proxy, and target coverage.
5. Generates capture and release windows.
6. Explains the recommendation and shows uncertainty and limitations.

The package includes a responsive browser UI, a Python model API, local interactive charts, four demonstration presets, model-evaluation metrics, and responsible-use guardrails.

## AI and technical design

The application uses a random-forest regressor trained on 1,226 real water-adsorption measurements from the NIST Isotherm Database (ISODB), spanning 12 materials and 13 published papers. The model estimates water uptake as a function of relative humidity, temperature, and material descriptors. A transparent sigmoid-style fallback formula is available if the model artifact cannot be loaded.

The scheduling layer scores each hour for moisture capture and water release. It selects contiguous operating windows and estimates daily water production using:

```text
W_day = MOF_mass x working_capacity x equivalent_cycles x device_efficiency
```

The browser sends the scenario to a local Python endpoint. The endpoint runs the climate, prediction, ranking, and scheduling pipeline and returns a structured JSON result to the UI.

## Data handling and evaluation

The packaged training script uses a group split by MOF name so rows from a held-out material are not mixed across training and test sets. The interface reports:

- Mean absolute error
- Root mean squared error
- R2
- Held-out MOFs
- Feature importance
- Model source and data limitation

The model is validated with leave-one-MOF-out cross-validation: for every material, it is held out completely and predicted from the other eleven. Held-out MAE is 0.119 kg/kg and R² is 0.24 — a modest but real signal for a screening tool, not a certified predictor. Performance varies by evidence tier; see `airwater/model_artifacts/metrics.json` and `docs/model_results_card.html` for the full breakdown.

## Innovation

Most material-screening tools focus on static material properties. AirWater AI evaluates the material in the climate and energy conditions where it would actually operate. Its core innovation is the connection between:

- Local hourly climate
- Material uptake prediction
- Regeneration constraints
- Explainable multi-objective ranking
- A 24-hour operating concept

## User experience

The application is designed for a live competition demonstration:

- One-click presets produce immediate, repeatable scenarios.
- The default mode works completely offline.
- A single recommendation card communicates the result clearly.
- Charts explain the climate and schedule.
- Candidate comparison shows why the top yield is not always the best feasible choice.
- A technical tab exposes model metrics instead of hiding the AI behind a chatbot.
- A responsible-use tab states exactly what the application cannot certify.

## Impact

AirWater AI can help research teams reduce an initial list of candidates to a smaller set for laboratory validation. It also helps students understand why a material can perform differently across dry, humid, tropical, and mild climates.

## Responsible AI and limitations

- Results are simulated planning estimates, not guarantees.
- The app does not manufacture, characterize, or certify MOFs.
- The app does not guarantee device-scale yield.
- The app does not declare water potable.
- Material degradation, chemical leaching, heat transfer, airflow, condenser performance, and water quality require further testing.
- Experimental, predicted, assumed, and missing information should remain visibly separated.

## Next milestones

1. Add condition holdout (hiding an RH or temperature band) and source-paper holdout validation, beyond the current point and leave-one-MOF-out holdouts.
2. Add calibrated prediction intervals or conformal uncertainty.
3. Validate recommendations against newly published, currently unseen experimental MOFs.
4. Add airflow, thermal, condenser, and cycling parameters to the device model.
5. Partner with a laboratory to test the highest-ranked candidate under controlled climate conditions.

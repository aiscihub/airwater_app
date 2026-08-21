**AirWater AI — Implementation Specification (Agent Handoff)**

Purpose
- Provide a concise, actionable implementation specification so another engineer or autonomous agent can continue building the AirWater AI system described in the project deck.

Scope
- Minimum-viable competition version (see Must-have list in deck). Focus on Modules A–D, the physics simulator, surrogate, optimizer, uncertainty/refusal logic, evaluation harness, and a simple API/demo.

Repository layout (expected)
- `data/` : climate, isotherms, materials, validation datasets
- `physics/` : isotherm.py, adsorption.py, regeneration.py, cycle.py, energy.py
- `climate/` : nasa_power.py, features.py, embeddings.py, archetypes.py
- `ml/` : dataset.py, surrogate.py, train.py, evaluate.py, uncertainty.py
- `optimization/` : schedule.py, material.py, objective.py
- `decision/` : viability.py, refusal.py, explanation.py
- `validation/` : field_trials.py, baselines.py, heldout_sites.py, verify_recommendation.py
- `app/` : api.py (FastAPI), dashboard.py (Streamlit)
- `experiments/` : notebooks and analysis scripts

High-level architecture
- Data sources → Climate processing (Module A) → Physics simulator (ground truth generator) → Surrogate model (Module B) → Optimizer (Module C) → Uncertainty & refusal (Module D) → Explainable recommendation → API / Demo

Core interfaces (function signatures)
- Physics simulator (ground truth):

  `simulate_cycle(material_params: dict, climate_hourly: pd.DataFrame, schedule: dict, energy_mode: str) -> dict`

  Returns: `{ 'yield_L_per_day': float, 'energy_kwh_per_l': float, 'diagnostics': {...} }`

- Climate embedding:

  `compute_climate_embedding(hourly_df: pd.DataFrame) -> np.array`

  Input: hourly DataFrame with columns `timestamp`, `temperature_C`, `relative_humidity_pct`, `solar_W_m2`. Output: PCA vector (~15–30 dims).

- Surrogate model API:

  `predict_yield_energy(features: dict) -> dict`

  Returns `{ 'pred_yield_L_per_kg': float, 'pred_energy_kwh_per_l': float, 'uncertainty': {...} }`

- Optimizer API:

  `optimize_for_site(materials: List[dict], climate_embedding: np.array, energy_mode: str, objective: dict, constraints: dict) -> dict`

  Returns best `{ 'material_id': str, 'schedule': {...}, 'predicted': {...}, 'runner_up': {...} }`

Data formats & feature definitions
- Climate hourly CSV: `timestamp,temperature_C,relative_humidity_pct,solar_W_m2`
- Monthly profile matrix: 12 × 24 × 3 flattened to 864 features, standardized then PCA.
- Material parameters: dictionary with fitted isotherm params, parameter covariance or bootstrap samples, max_operating_temp, mass_per_device (kg), identity tag.
- Schedule `θ`: `{ 'adsorb_start': 'HH:MM', 'adsorb_end': 'HH:MM', 'regenerate_start': 'HH:MM', 'regenerate_end': 'HH:MM', 'T_regen_C': float }`

Climate processing (Module A) — implementation details
- Preprocess hourly data: ensure timezone-aware timestamps, fill missing hourly records, optionally average multiple years.
- Derived stats: compute percentiles of RH, counts of hours above thresholds (20/30/40/50%), nighttime/daytime splits, humid_hour_count, solar_hour_count, RH-solar overlap, lag between RH and solar peaks.
- Monthly 24-hour profiles: group by month and hour; compute mean T, RH, solar; stack to 12×24×3 matrix.
- PCA: standardize features, run PCA, keep components covering ~90–95% variance (target 15–30 components). Save PCA transformer at `data/embeddings/pca.pkl`.
- Clustering: run KMeans for K in [3..12], compute silhouette scores, cluster stability (multiple seeds), and select K by a small heuristic. Save `climate_archetype` labels.

Physics simulator — ground-truth generator
- Input: material isotherm params (plus uncertainty samples), hourly climate, schedule θ, device mass.
- Behavior: simulate adsorption/desorption mass transfer per hour or per event window using fitted isotherm relationships and energy balance for regeneration.
- Output: daily yield per kg (or per device), energy consumption per cycle, failure flags (e.g., required T_regen > max material/device temperature), and time series diagnostics.

Dataset generation & sampling
- Use Latin Hypercube or Sobol sampling across schedule parameters (adsorb start/end windows, regen temp) and energy modes.
- For each (material, site, schedule) call the physics simulator to generate (Y,E). Store labeled records in `ml/datasets/` with metadata.

Surrogate modeling (Module B)
- Models: baseline linear regression, random forest, primary: XGBoost regressor. Optionally a small MLP as a check.
- Training split: held-out-location split (train on 80% of locations, test on 20% unseen locations). Also prepare geographic/archetype holdouts for stronger tests.
- Features: material physical params (no plain name), climate embedding + selected stats, schedule numeric encodings (start in hour fractions, durations), energy_mode as categorical.
- Metrics: MAE, RMSE, R² for continuous predictions; Top-1/Top-3 material accuracy, Spearman/Kendall for ranking.
- Model artifact: save trained model, feature list, and preprocessor objects to `ml/models/`.

Optimizer (Module C) — implementation details
- For each candidate material: run a Bayesian optimizer (Optuna TPE) to find schedule θ that minimizes objective (cost or energy) under constraints.
- Objective: default `min cost_per_L` subject to `yield_L_per_day >= target` and `energy/day <= available_energy`.
- Search space: adsorb start ∈ [0,23.5] (hour), adsorb duration ∈ [1,16] (hours), regenerate start/duration consistent, T_regen ∈ [material_min, material_max]. Enforce no-overlap by constraints or penalized objective.
- Use surrogate for cheap objective evaluation; after optimization, verify candidate schedules with the full physics simulator and compute regret.

Uncertainty engine (Module D)
- Material parameter uncertainty: sample 100–500 parameter draws from fit covariance or bootstrap distribution; for each draw, predict with surrogate to obtain yield distribution.
- ML uncertainty: train an ensemble of N models (e.g., 5) with bootstrap resamples / different seeds; use spread as another uncertainty source.
- Combine uncertainties (e.g., via sampling) and apply split-conformal calibration on held-out validation set to obtain empirical 90% intervals.
- Save calibration parameters and report actual coverage in evaluation.

Refusal logic
- Refuse (DO NOT DEPLOY) when any of:
  - Lower 90% CI for yield < requested Y_target
  - Cost per L > alternative_cost_per_l
  - Required regeneration energy > available energy (given energy_mode)
  - CI width > τ (documented threshold, e.g., 30% relative)
  - OOD climate distance (Mahalanobis distance in PCA space above threshold)

Explainability & counterfactuals
- Produce evidence bullets: match between material isotherm shape and RH schedule; regen temperature fit; numeric comparisons to runner-up.
- Counterfactual: compute predictions for a specified alternative material and return differences in yield/energy + which physics features drive the difference.
- SHAP: compute global importances for reporting.

API contract (demo)
- Request JSON:

  ```json
  { "latitude": 33.4484, "longitude": -112.0740, "energy_source": "solar", "water_target_lpd": 5, "sorbent_mass_kg": 10, "alternative_cost_per_l": 0.50 }
  ```

- Response JSON (fields required):

  ```json
  {
    "decision": "VIABLE" | "NOT_VIABLE",
    "material": "<material_id>",
    "climate_archetype": <int>,
    "schedule": { "adsorb_start": "20:00", "adsorb_end": "07:00", "regenerate_start": "11:00", "regenerate_end": "15:00", "T_regen_C": 75 },
    "yield_lpd": 4.1,
    "yield_90_ci": [3.3, 4.9],
    "energy_kwh_per_l": 1.8,
    "cost_per_l": 0.37,
    "runner_up": "MOF-303",
    "explanation": ["Higher predicted annual yield under this climate", "Better match to nighttime humidity", "Lower regeneration-energy requirement"]
  }
  ```

Validation & experiments
- Hold-out-location evaluation for surrogate and optimizer.
- Baseline experiments: Peak-uptake ranking, fixed schedules, physics-hourly fixed schedules.
- Optimization verification: run `validation/verify_recommendation.py` to run physics on AI-recommended (m*,θ*) and compute regret.

Developer entry points & commands
- Install:

  ```bash
  python -m venv venv
  source venv/bin/activate
  pip install -r requirements.txt
  ```

- Generate dataset (example):

  ```bash
  python physics/generate_dataset.py --materials data/materials/*.json --climate data/climate/*csv --samples-per-config 20 --out ml/datasets/ds.parquet
  ```

- Train surrogate:

  ```bash
  python ml/train.py --dataset ml/datasets/ds.parquet --model xgboost --out ml/models/xgb.pkl
  ```

- Run optimizer demo for a site (FastAPI):

  ```bash
  uvicorn app.api:app --reload --port 8000
  # then POST to /recommend
  ```

Agent handoff checklist
- Confirm `data/` contains climate hourly CSVs and `data/isotherms/` contains fitted parameter files and covariance/bootstraps.
- Confirm `physics/simulate_cycle` runs and returns plausible outputs for a few known (material, climate, schedule) tuples.
- Confirm PCA transformer and surrogate model artifacts exist or run the dataset generation + training steps above.
- Run validation harness: `python validation/verify_recommendation.py --site <lat,lon>` and inspect regression metrics and regret.

Notes & decisions
- Use XGBoost as primary surrogate baseline; keep neural nets optional.
- Use Optuna for Bayesian optimization (TPE) for each material, verify best candidate with physics.
- Use split conformal calibration for interval calibration; record empirical coverage.
- Start with Streamlit demo, FastAPI backend later as needed.

Contact & next steps
- To continue: implement `app/api.py` endpoint that wraps `optimize_for_site` and returns the JSON contract above; add regression test asserting required fields and CI presence.

File added by agent: [docs/implementation_spec.md](docs/implementation_spec.md)

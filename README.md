# AirWater AI

AirWater AI is a functional proof-of-concept application for climate-aware selection of metal-organic frameworks (MOFs) for atmospheric water harvesting.

The browser interface accepts a location, season, MOF mass, water target, energy source, regeneration-temperature limit, and device-efficiency assumption. The Python model API then:

1. Builds a 24-hour climate profile.
2. Predicts water uptake for each candidate MOF.
3. Estimates working capacity and simulated daily water yield.
4. Ranks candidates using yield, climate fit, regeneration feasibility, evidence, stability, and cost proxies.
5. Generates capture and release windows.
6. Returns explanations, confidence labels, model metrics, and responsible-use warnings.

## Important scope statement

AirWater AI is a research-screening application. It does not manufacture, characterize, certify, or guarantee a MOF or a water-harvesting device. It does not declare harvested water potable. All output requires laboratory, engineering, safety, and water-quality validation.

## Demo-ready features

- Polished responsive browser UI.
- Four one-click competition presets.
- Fully offline default mode for reliable live demonstrations.
- Optional NASA POWER historical-sample mode when internet access is available.
- Interactive climate chart with capture and release windows.
- Explainable top recommendation and target-coverage display.
- Top-three candidate cards, comparison chart, and detailed evidence view.
- AI-model page with group-split metrics and feature importance.
- Clear responsible-use and validation roadmap.
- Lightweight Python server using only the standard-library HTTP server.

## Quick start

### 1. Install Python dependencies

```bash
python -m pip install -r requirements.txt
```

### 2. Start the application

```bash
python app.py
```

The application opens at:

```text
http://127.0.0.1:8000
```

Alternative launchers:

- macOS/Linux: `./run_demo.sh`
- Windows: `run_demo.bat`

To prevent the browser from opening automatically:

```bash
python app.py --no-browser
```

To use another port:

```bash
python app.py --port 8080
```

## Recommended live-demo sequence

1. Open the default **Desert solar** preset.
2. Explain the Phoenix climate profile and the overnight capture window.
3. Show the top recommendation, yield range, target coverage, and heat margin.
4. Open **Candidate comparison** and explain why the highest theoretical yield does not always rank first when heat limits are considered.
5. Select the **Mild hybrid** preset to demonstrate that the ranking can change with climate and energy constraints.
6. Open **AI model and evidence** to show evaluation metrics, held-out MOFs, and feature importance.
7. End on **Responsible use** to clarify that the app prioritizes materials for testing rather than certifying them.

## Architecture

```text
Browser UI (HTML, CSS, JavaScript, local Plotly)
                 |
                 | POST /api/analyze
                 v
Python standard-library HTTP server
                 |
       Climate profile generator
                 |
    Water-uptake prediction model
                 |
 Ranking and schedule optimization logic
                 |
  JSON recommendation and evidence response
```

## Folder structure

```text
airwater_app/
  app.py                         Main launcher
  server.py                      Local API and static-file server
  requirements.txt               Python dependencies
  run_demo.sh                    macOS/Linux launcher
  run_demo.bat                   Windows launcher
  web/
    index.html                   Browser application
    styles.css                   Responsive visual design
    app.js                       UI behavior and charts
    vendor/plotly.min.js         Local charting library
  airwater/
    climate.py                   Demo and optional NASA climate data
    selector.py                  Prediction, ranking, and scheduling
    model_artifacts/             Demo model, metrics, and training rows
  data/
    candidate_mofs.csv           Simplified candidate descriptors
  scripts/
    train_demo_model.py          Rebuild the demonstration model
    smoke_test.py                Test all application presets
  docs/
    competition_submission.md    Draft project description
    demo_script.md               Five-minute walkthrough
    judge_demo_checklist.md      Pre-presentation checklist
    source_attribution.md        Research and data references
    ui_preview.png               Interface preview
```

## Model and data limitations

The packaged random-forest model is trained on synthetic demonstration rows generated from simplified candidate curves. This is sufficient to demonstrate the complete AI application workflow, but it is not sufficient for scientific performance claims.

For a final research-grade version, replace the demonstration rows with curated experimental water-adsorption isotherms and validate using complete-MOF holdouts or leave-one-MOF-out testing. The device model should also be extended to cover heat transfer, airflow, condenser performance, degradation, fouling, and water-quality risks.

## Optional model retraining

```bash
python scripts/train_demo_model.py
```

## Smoke test

```bash
python scripts/smoke_test.py
```

The smoke test runs all four UI presets and checks the response contract, ranking, schedule, and model metadata.

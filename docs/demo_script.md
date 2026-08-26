# Five-Minute AirWater AI Demo Script

Setup: app open on the Analysis tab, nothing run yet.

## 0:00-0:30 - Opening

We're Team [Name]. AirWater AI helps researchers prioritize which metal-organic framework to test first for atmospheric water harvesting.

A MOF with a great lab uptake number can still fail in the field -- it depends on local humidity, available heat, cycle time, stability, and how much evidence actually backs that number. AirWater AI screens for all of that, not just uptake.

## 0:30-1:00 - Load a scenario

Click the **Desert solar** preset. Point at the filled-in fields:

- Phoenix, Arizona, July
- 10 kg of material
- 3 liters/day target
- 185 F maximum regeneration temperature
- Solar-only energy

> This is Phoenix in July: 10 kg of material, a 3-liter-a-day target, 185 F max regeneration heat, solar-only power. Every one of these is a real constraint the model has to satisfy, not just an uptake curve. That 185 F heat limit matters more than it looks -- nudge it up or down and the recommended MOF itself can change, because it's what decides which materials can even regenerate on this site's heat source.

Click **Run AirWater analysis**.

## 1:00-1:45 - The recommendation

Point at the verdict banner, then the daily operating plan chart.

> The top pick is Aluminum fumarate -- predicted yield 3.98 to 5.16 liters a day, with High confidence. Notice it's a range, not a single number, and the decision banner says VIABLE because it cleared every gate: yield, regeneration heat, uncertainty, climate fit, cost, and evidence quality.

Point at the capture/release timeline.

> This is the actual 24-hour schedule: green is when it's adsorbing water, gold is when it releases and condenses it -- timed against this site's real humidity and solar curve.

## 1:45-2:30 - Compare MOFs

Click the **Compare MOFs** tab.

> AirWater doesn't just show the winner -- it ranks all fifteen candidates and explains why each one lost.

Scroll to MOF-801, point at its caution pill.

> This one's a good example of why we built confidence into the ranking, not just yield. MOF-801's point estimate also clears the target -- but its range is 0.90 to 7.96 liters a day, and it's flagged "Candidate for laboratory testing -- proceed with caution." No NIST water isotherm exists for this material at all; the model is extrapolating from other MOFs' chemistry. We don't hide that uncertainty behind a clean-looking number.

## 2:30-3:15 - Why this recommendation

Click the **Why this recommendation** tab, point at the evidence breakdown.

> Every score here is auditable. Aluminum fumarate's evidence score is 78 out of 100, built from three real NIST ISODB water isotherms, one published paper, and 59 measured points -- you can see exactly where each point came from.
>
> The model itself is validated by leave-one-MOF-out cross-validation -- for every material, we hide it completely and ask the model to predict it from the other eleven. Held-out error is 0.119 kg/kg, R-squared of 0.24. That's real, and it's modest -- this is a screening tool, not a certified predictor.

## 3:15-4:00 - The refusal gate

Switch to the **Mild hybrid** preset (Nairobi) and run it. The verdict banner should read **DO NOT DEPLOY**.

> Now watch what happens somewhere the model is less sure of itself. Same top-ranked material, same-looking point estimate -- but here the app refuses. The 90% lower bound drops to 1.63 liters against a 3-liter target, the prediction interval blows out past our own confidence threshold, and the site itself falls outside the climate conditions the model was trained on. Three separate checks fail, so it says no. AirWater is built to be allowed to say no -- that's a guardrail, not just a disclaimer.

## 4:00-4:35 - Responsible use and data

Click the **Responsible use** tab, then briefly the **Material library** / **Data & assumptions** nav items.

> We're explicit about the boundary: AirWater can prioritize candidates and generate a testing plan. It cannot manufacture, certify, or guarantee a MOF, and it doesn't replace lab or engineering validation. Every material and every assumption behind these numbers is browsable here, not buried.

## 4:35-5:00 - Closing

> AirWater AI turns local climate and real experimental data into an explainable, honest screening decision -- which material to test first, under what conditions, and when it should say no. That honesty is the whole point.

---

**Delivery notes:**

- The Desert -> Mild-hybrid pivot (VIABLE -> DO NOT DEPLOY) is the strongest beat in the script -- it's a live demonstration of the refusal gate actually working, not a claim. Don't rush it.
- If asked about training data: the model trains on real NIST ISODB water isotherms -- 1,226 measured points across 12 materials and 13 published papers, validated with leave-one-MOF-out cross-validation. There is no synthetic training data in the current build.

# Five-Minute AirWater AI Demo Script

## 0:00-0:25 - Opening

Hello, we are Team [Name]. Our project is AirWater AI, a functional AI application that helps research teams prioritize metal-organic frameworks for atmospheric water harvesting.

The problem is that a MOF with a strong laboratory uptake value may not be the best material in a real location. Performance also depends on humidity, temperature, available heat, cycle timing, stability, and device losses.

## 0:25-0:55 - Show the application workflow

At the top of the interface, show the four-step workflow:

1. Read local climate conditions.
2. Predict water uptake.
3. Rank candidate MOFs.
4. Generate an operating plan.

State clearly:

> This application screens candidates for laboratory validation. It does not manufacture or certify the MOF.

## 0:55-1:30 - Load the desert scenario

Select **Desert solar**.

Point out the inputs:

- Phoenix, Arizona
- July
- 10 kg of MOF
- 3 liters per day target
- 85 C maximum regeneration temperature
- Solar-only energy
- 55 percent device-efficiency assumption

Click **Run AirWater analysis**.

## 1:30-2:15 - Explain the top recommendation

Show the top recommendation card.

Explain:

- The application reports a simulated range rather than one guaranteed number.
- Target coverage indicates whether the central estimate reaches the selected goal.
- The regeneration margin shows whether the user heat limit is compatible with the material target.
- The evidence and stability ratings influence the final ranking.

Use this sentence:

> AirWater AI does not ask only which MOF can hold the most water. It asks which candidate best fits this climate and operating constraint.

## 2:15-3:00 - Show the climate and operating plan

Open **Climate and plan**.

Point out:

- Relative humidity rises during the cooler nighttime period.
- Solar availability rises during the day.
- Green shading identifies the proposed capture window.
- Gold shading identifies the proposed release and condensation window.
- The timeline converts the climate data into a simple 24-hour operating concept.

## 3:00-3:40 - Compare candidates

Open **Candidate comparison**.

Show the top-three cards and comparison chart. Explain that bubble size represents simulated yield, while the axes show working capacity and regeneration target.

Select another candidate in the evidence panel and show:

- Climate fit
- Target coverage
- Capture uptake
- Residual uptake
- Main limitation

Then choose the **Mild hybrid** preset. Explain that a different heat limit and energy source can change the ranking, demonstrating that the result is not hardcoded.

## 3:40-4:25 - Show the AI model

Open **AI model and evidence**.

Explain:

- The prototype uses a random-forest water-uptake regressor.
- The evaluation uses a group split by MOF name to reduce leakage between training and test rows.
- MAE, RMSE, and R2 are displayed in the app.
- Feature importance shows that relative humidity is the strongest input in the demonstration model.

State the limitation honestly:

> The packaged training rows are synthetic demonstration data. Our next scientific milestone is replacing them with curated experimental isotherms.

## 4:25-4:50 - Responsible use

Open **Responsible use**.

Explain that the app can prioritize materials and generate research hypotheses, but cannot:

- Manufacture or certify a MOF
- Guarantee full-device output
- Rule out degradation or chemical leaching
- Declare water potable
- Replace laboratory, engineering, or regulatory review

## 4:50-5:00 - Closing

> AirWater AI turns local climate into an explainable material-screening and operating plan. It helps researchers test the right material, under the right conditions, at the right time.

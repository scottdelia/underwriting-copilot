# Eval dataset review

**These labels are generated and have not been verified by a human.**

They come from `tools/eval_oracle.py`, which computes outcomes from the
published thresholds in `tools/carrier_data.py`. No pipeline output was
consulted, so the labels are independent of the system under test.

They are not independent of their author. The oracle and the synthesis
prompt were written by the same person from the same documents, so a
rule misread in one may be misread in the other, and the two would
agree while both were wrong. That is what this review is for.

## How to review

For each item, open the cited page of the carrier's PDF in `corpus/`
and check that the expected class follows from what is printed. Tick
the box when you have. The build chart items are the fastest to check
and the cross-carrier ones carry the most weight.

**50 items total.**

## build_chart (12)

- [ ] **eval_001** What is the maximum weight at 5'10" for Northstar Mutual Life Preferred Elite male?
      expected: `Preferred Elite = 189 lb`
      cite: northstar p3
      note: Edge case: 5'10" is the a mid-chart row.
- [ ] **eval_002** What is the maximum weight at 5'10" for Northstar Mutual Life Standard Plus male?
      expected: `Standard Plus = 213 lb`
      cite: northstar p3
      note: Edge case: 5'10" is the a mid-chart row.
- [ ] **eval_003** What is the maximum weight at 5'4" for Northstar Mutual Life Preferred female?
      expected: `Preferred = 163 lb`
      cite: northstar p2
      note: Edge case: 5'4" is the a mid-chart row.
- [ ] **eval_004** What is the maximum weight at 6'0" for Cardinal Assurance Select NT?
      expected: `Select NT = 233 lb`
      cite: cardinal p2
      note: Edge case: 6'0" is the a mid-chart row.
- [ ] **eval_005** What is the maximum weight at 5'6" for Cardinal Assurance Super Preferred NT?
      expected: `Super Preferred NT = 170 lb`
      cite: cardinal p2
      note: Edge case: 5'6" is the a mid-chart row.
- [ ] **eval_006** What is the maximum weight at 5'10" for Meridian Life & Annuity Standard Plus male?
      expected: `Standard Plus = 215 lb`
      cite: meridian p3
      note: Edge case: 5'10" is the a mid-chart row.
- [ ] **eval_007** What is the maximum weight at 5'8" for Meridian Life & Annuity Preferred Plus female?
      expected: `Preferred Plus = 172 lb`
      cite: meridian p2
      note: Edge case: 5'8" is the a mid-chart row.
- [ ] **eval_008** What is the maximum weight at 6'2" for Granite Peak Financial Standard?
      expected: `Standard = 265 lb`
      cite: granite p2
      note: Edge case: 6'2" is the a mid-chart row.
- [ ] **eval_009** What is the maximum weight at 5'2" for Granite Peak Financial Elite?
      expected: `Elite = 148 lb`
      cite: granite p2
      note: Edge case: 5'2" is the a mid-chart row.
- [ ] **eval_010** What is the maximum weight at 4'8" for Northstar Mutual Life Standard male?
      expected: `Standard = 145 lb`
      cite: northstar p2
      note: Edge case: 4'8" is the first row of the chart.
- [ ] **eval_011** What is the maximum weight at 6'8" for Meridian Life & Annuity Preferred male?
      expected: `Preferred = 262 lb`
      cite: meridian p3
      note: Edge case: 6'8" is the last row of the chart.
- [ ] **eval_012** What is the maximum weight at 6'8" for Cardinal Assurance Standard NT?
      expected: `Standard NT = 300 lb`
      cite: cardinal p2
      note: Edge case: 6'8" is the last row of the chart.

## single_condition (12)

- [ ] **eval_013** For Northstar Mutual Life, how would a 50 year old male at 5'10" and 175 lb with type 2 diabetes, A1c 6.5 be classified?
      expected: `northstar=standard_plus`
      cite: northstar p4
      note: Build allows preferred_plus; the condition allows standard_plus; below the 7.0 threshold.
- [ ] **eval_014** For Northstar Mutual Life, how would a 50 year old male at 5'10" and 175 lb with type 2 diabetes, A1c 7.4 be classified?
      expected: `northstar=standard`
      cite: northstar p4
      note: Build allows preferred_plus; the condition allows standard; inside the 7.0-7.9 band.
- [ ] **eval_015** For Northstar Mutual Life, how would a 50 year old male at 5'10" and 175 lb with type 2 diabetes, A1c 8.5 be classified?
      expected: `northstar=table_rated`
      cite: northstar p4
      note: Build allows preferred_plus; the condition allows table_rated; inside the 8.0-8.9 band.
- [ ] **eval_016** For Northstar Mutual Life, how would a 50 year old male at 5'10" and 175 lb with type 2 diabetes, A1c 9.3 be classified?
      expected: `northstar=decline`
      cite: northstar p4
      note: Build allows preferred_plus; the condition allows decline; above the eligibility cut.
- [ ] **eval_017** For Cardinal Assurance, how would a 50 year old at 5'10" and 180 lb with type 2 diabetes, A1c 6.4, BMI 25.8 be classified?
      expected: `cardinal=preferred`
      cite: cardinal p4
      note: Build allows preferred_plus; the condition allows preferred; best grid cell.
- [ ] **eval_018** For Cardinal Assurance, how would a 50 year old at 5'10" and 190 lb with type 2 diabetes, A1c 7.2, BMI 27.3 be classified?
      expected: `cardinal=standard_plus`
      cite: cardinal p4
      note: Build allows preferred_plus; the condition allows standard_plus; middle grid cell.
- [ ] **eval_019** For Cardinal Assurance, how would a 50 year old at 5'10" and 190 lb with type 2 diabetes, A1c 8.2, BMI 27.3 be classified?
      expected: `cardinal=standard`
      cite: cardinal p4
      note: Build allows preferred_plus; the condition allows standard; poor control.
- [ ] **eval_020** For Meridian Life & Annuity, how would a 50 year old male at 5'10" and 180 lb with type 2 diabetes, A1c 6.8, diagnosed 4 years ago be classified?
      expected: `meridian=standard_plus`
      cite: meridian p4
      note: Build allows preferred_plus; the condition allows standard_plus; short duration.
- [ ] **eval_021** For Meridian Life & Annuity, how would a 50 year old male at 5'10" and 180 lb with type 2 diabetes, A1c 6.8, diagnosed 12 years ago be classified?
      expected: `meridian=standard`
      cite: meridian p4
      note: Build allows preferred_plus; the condition allows standard; long duration.
- [ ] **eval_022** For Granite Peak Financial, how would a 50 year old at 5'10" and 190 lb with type 2 diabetes, A1c 7.0, BMI 27.3 be classified?
      expected: `granite=standard`
      cite: granite p4
      note: Build allows preferred; the condition allows standard; under the BMI cut.
- [ ] **eval_023** For Northstar Mutual Life, how would a 50 year old male at 5'10" and 175 lb with obstructive sleep apnea, compliant on CPAP be classified?
      expected: `northstar=standard_plus`
      cite: northstar p5
      note: Build allows preferred_plus; the condition allows standard_plus; rule names a class outright.
- [ ] **eval_024** For Meridian Life & Annuity, how would a 50 year old male at 5'10" and 175 lb with mild intermittent asthma using a rescue inhaler twice a week be classified?
      expected: `meridian=preferred_plus`
      cite: meridian p4
      note: Build allows preferred_plus; the condition allows preferred_plus; rule names a class outright.

## multi_condition (10)

- [ ] **eval_025** For Northstar Mutual Life: 50 year old male, 5'10", 200 lb, with type 2 diabetes, A1c 7.2 and treated hypertension averaging 138/84, stable for two years. How would they be classified?
      expected: `northstar=standard`
      cite: northstar p4, northstar p4
      note: Build allows standard_plus; type_2_diabetes allows standard; hypertension allows preferred; the worst of the three governs.
- [ ] **eval_026** For Northstar Mutual Life: 50 year old male, 5'10", 200 lb, with type 2 diabetes, A1c 6.5 and obstructive sleep apnea, compliant on CPAP. How would they be classified?
      expected: `northstar=standard_plus`
      cite: northstar p4, northstar p5
      note: Build allows standard_plus; type_2_diabetes allows standard_plus; obstructive_sleep_apnea allows standard_plus; the worst of the three governs.
- [ ] **eval_027** For Northstar Mutual Life: 50 year old male, 6'2", 235 lb, with type 2 diabetes, A1c 8.4 and treated hypertension averaging 138/84, stable for two years. How would they be classified?
      expected: `northstar=table_rated`
      cite: northstar p4, northstar p4
      note: Build allows standard_plus; type_2_diabetes allows table_rated; hypertension allows preferred; the worst of the three governs.
- [ ] **eval_028** For Cardinal Assurance: 50 year old, 5'10", 190 lb, with type 2 diabetes, A1c 7.1, BMI 27.3 and elevated cholesterol with a total-to-HDL ratio of 4.8 on a statin. How would they be classified?
      expected: `cardinal=standard_plus`
      cite: cardinal p4, cardinal p4
      note: Build allows preferred_plus; type_2_diabetes allows standard_plus; hyperlipidemia allows preferred_plus; the worst of the three governs.
- [ ] **eval_029** For Cardinal Assurance: 50 year old, 5'10", 190 lb, with type 2 diabetes, A1c 6.4, BMI 27.3 and isolated atrial fibrillation, rate controlled, stably anticoagulated for two years, with no structural heart disease. How would they be classified?
      expected: `cardinal=standard`
      cite: cardinal p4, cardinal p5
      note: Build allows preferred_plus; type_2_diabetes allows preferred; atrial_fibrillation allows standard; the worst of the three governs.
- [ ] **eval_030** For Cardinal Assurance: 50 year old, 5'6", 205 lb, with type 2 diabetes, A1c 8.2, BMI 33.1 and elevated cholesterol with a total-to-HDL ratio of 4.8 on a statin. How would they be classified?
      expected: `cardinal=table_rated`
      cite: cardinal p4, cardinal p4
      note: Build allows None; type_2_diabetes allows table_rated; hyperlipidemia allows preferred_plus; the worst of the three governs.
- [ ] **eval_031** For Meridian Life & Annuity: 50 year old male, 5'10", 195 lb, with type 2 diabetes, A1c 6.8, diagnosed 3 years ago and mild intermittent asthma using a rescue inhaler twice a week. How would they be classified?
      expected: `meridian=standard_plus`
      cite: meridian p4, meridian p4
      note: Build allows preferred; type_2_diabetes allows standard_plus; asthma allows preferred_plus; the worst of the three governs.
- [ ] **eval_032** For Meridian Life & Annuity: 50 year old male, 5'10", 195 lb, with type 2 diabetes, A1c 7.5, diagnosed 3 years ago and a single myocardial infarction eight years ago with a normal ejection fraction and a negative recent stress test. How would they be classified?
      expected: `meridian=table_rated`
      cite: meridian p4, meridian p4
      note: Build allows preferred; type_2_diabetes allows standard; myocardial_infarction allows table_rated; the worst of the three governs.
- [ ] **eval_033** For Granite Peak Financial: 50 year old, 5'10", 200 lb, with type 2 diabetes, A1c 7.0, BMI 28.7 and hepatitis C treated to sustained virologic response three years ago, with normal liver enzymes and no fibrosis. How would they be classified?
      expected: `granite=standard`
      cite: granite p4, granite p4
      note: Build allows preferred; type_2_diabetes allows standard; hepatitis_c allows standard_plus; the worst of the three governs.
- [ ] **eval_034** For Granite Peak Financial: 50 year old, 5'10", 225 lb, with type 2 diabetes, A1c 7.8, BMI 32.3 and hepatitis C treated to sustained virologic response three years ago, with normal liver enzymes and no fibrosis. How would they be classified?
      expected: `granite=table_rated`
      cite: granite p4, granite p4
      note: Build allows standard; type_2_diabetes allows table_rated; hepatitis_c allows standard_plus; the worst of the three governs.

## cross_carrier (8)

- [ ] **eval_035** 55 year old male, 5'10", 216 lb, type 2 diabetes with an A1c of 7.1, non-smoker, $500K 20-year term. Compare the carriers.
      expected: `northstar=standard, cardinal=standard_plus, meridian=standard, granite=table_rated`
      cite: northstar p4, cardinal p4, meridian p4, granite p4
      note: the brief's demo scenario, stated by BMI
- [ ] **eval_036** 45 year old female, 5'5", 150 lb, type 2 diabetes with an A1c of 6.4, non-smoker, $500K 20-year term. Compare the carriers.
      expected: `northstar=standard_plus, cardinal=preferred, meridian=standard_plus, granite=standard`
      cite: northstar p4, cardinal p4, meridian p4, granite p4
      note: well-controlled, favourable build
- [ ] **eval_037** 60 year old male, 6'0", 260 lb, type 2 diabetes with an A1c of 8.2, non-smoker, $500K 20-year term. Compare the carriers.
      expected: `northstar=table_rated, cardinal=table_rated, meridian=table_rated, granite=decline`
      cite: northstar p4, cardinal p4, meridian p4, granite p4
      note: poor control and a heavy build
- [ ] **eval_038** 38 year old male, 5'8", 170 lb, type 2 diabetes with an A1c of 6.8, non-smoker, $500K 20-year term. Compare the carriers.
      expected: `northstar=standard_plus, cardinal=standard_plus, meridian=standard_plus, granite=standard`
      cite: northstar p4, cardinal p4, meridian p4, granite p4
      note: young, borderline on Meridian's cut
- [ ] **eval_039** 52 year old female, 5'3", 190 lb, type 2 diabetes with an A1c of 7.6, non-smoker, $500K 20-year term. Compare the carriers.
      expected: `northstar=standard, cardinal=table_rated, meridian=standard, granite=table_rated`
      cite: northstar p4, cardinal p4, meridian p4, granite p4
      note: crosses Cardinal's 7.5 boundary
- [ ] **eval_040** 58 year old male, 6'2", 230 lb, type 2 diabetes with an A1c of 6.9, non-smoker, $500K 20-year term. Compare the carriers.
      expected: `northstar=standard_plus, cardinal=standard_plus, meridian=standard_plus, granite=standard`
      cite: northstar p4, cardinal p4, meridian p4, granite p4
      note: on Meridian's exact 6.9 threshold
- [ ] **eval_041** 49 year old male, 5'9", 205 lb, type 2 diabetes with an A1c of 9.5, non-smoker, $500K 20-year term. Compare the carriers.
      expected: `northstar=decline, cardinal=table_rated, meridian=decline, granite=table_rated`
      cite: northstar p4, cardinal p4, meridian p4, granite p4
      note: above every carrier's eligibility cut
- [ ] **eval_042** 41 year old female, 5'7", 160 lb, type 2 diabetes with an A1c of 7.0, non-smoker, $500K 20-year term. Compare the carriers.
      expected: `northstar=standard, cardinal=standard_plus, meridian=standard, granite=standard`
      cite: northstar p4, cardinal p4, meridian p4, granite p4
      note: on Northstar's exact 7.0 boundary

## out_of_corpus (8)

- [ ] **eval_043** What is the average auto insurance premium in Ohio?
      expected: `must abstain`
      note: A different line of insurance entirely.
- [ ] **eval_044** How much would a $500,000 20-year term policy cost per month for a 55 year old male?
      expected: `must abstain`
      note: Premiums are out of scope; the guides carry no rates.
- [ ] **eval_045** Should my client buy whole life or term insurance for retirement?
      expected: `must abstain`
      note: Advice, not a published guideline.
- [ ] **eval_046** How would Northstar classify an applicant with stage 3 chronic kidney disease?
      expected: `must abstain`
      note: A plausible condition that no indexed guide addresses.
- [ ] **eval_047** What is Meridian's underwriting position on scuba diving to 200 feet?
      expected: `must abstain`
      note: An avocation none of the guides covers.
- [ ] **eval_048** Which carrier has the best customer service ratings?
      expected: `must abstain`
      note: Not an underwriting question.
- [ ] **eval_049** What did Cardinal Assurance's 2025 annual report say about profitability?
      expected: `must abstain`
      note: A document that is not in the corpus.
- [ ] **eval_050** How would Granite Peak classify a 40 year old with a history of melanoma?
      expected: `must abstain`
      note: A condition outside the indexed vocabulary and the guides.

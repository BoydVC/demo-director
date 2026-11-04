# Verifier — pick a base, graft the best, open the winner
Repo: BoydVC/demo-director. Inputs: the three draft PRs from arena/types-first, arena/model-first, arena/experience-first.

1. Check out each branch, run `python -m unittest discover -s tests`, run batch on examples/, record pass/fail, GIF sizes, frame counts, and look at each gallery screenshot.
2. Score each on: SPEC v1 acceptance (all 6 goals), test strength (behaviour, not implementation), PII guard fails closed, output quality of the GIFs, code simplicity.
3. Pick the strongest base. Graft: types from the types-first seat, state-model properties/tests from model-first, visual polish + gallery + init UX from experience-first, where better than the base.
4. Open ONE draft PR from arena/winner with a table of what came from where and proof (test output, regenerated shots/demo-*.gif, gallery screenshot). Do not merge. Do not close seat PRs. Do not post anywhere. Synthetic data only.

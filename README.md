# Weinstein Stage 2 dashboard

Runs every Friday (GitHub Actions), screens the S&P 1500 using the rules in
*Secrets for Profiting in Bull and Bear Markets*, and publishes `docs/index.html`
via GitHub Pages. Not investment advice.

Setup: push this folder to a new repo, then Settings > Pages > Deploy from branch > `main` / `/docs`.
Settings > Actions > General > Workflow permissions: Read and write.
Run it once by hand: Actions > Weekly Weinstein screen > Run workflow.

## Open positions

Positions live in `positions.csv` (public, no share counts or dollar values). On the dashboard, open **Owner: log a trade**, enter the buy (or pick a position to close), and press the button: it copies the CSV line and opens the file's GitHub edit page, where you paste it and commit. Only the repository owner can commit, so visitors cannot add or change anything. A small workflow (`positions.yml`) refreshes the positions panel within about a minute of each change; the Friday run refreshes it too.

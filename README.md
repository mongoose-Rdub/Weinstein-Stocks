# Weinstein Stage 2 dashboard

Runs every Friday (GitHub Actions), screens the S&P 1500 using the rules in
*Secrets for Profiting in Bull and Bear Markets*, and publishes `docs/index.html`
via GitHub Pages. Not investment advice.

Setup: push this folder to a new repo, then Settings > Pages > Deploy from branch > `main` / `/docs`.
Settings > Actions > General > Workflow permissions: Read and write.
Run it once by hand: Actions > Weekly Weinstein screen > Run workflow.

@echo off
REM Push current branch to origin. No --force: history rewrites must be a conscious manual action.
set PATH=C:\Program Files\Git\cmd;%PATH%
git status --short
echo.
set /p MSG="Commit message (leave empty to push without committing): "
if not "%MSG%"=="" (
  git add -A
  git commit -m "%MSG%"
)
git push -u origin main
echo Deployment push complete!
pause

@echo off
set PATH=C:\Program Files\Git\cmd;%PATH%
git add .
git commit -m "Final tank mode deployment - clean code with proper settings"
git remote remove origin 2>nul
git remote add origin https://github.com/leogonnn-web/bot6.git
git branch -M main
git push -u origin main --force
echo Deployment complete!
pause

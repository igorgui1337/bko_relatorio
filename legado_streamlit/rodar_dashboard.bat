@echo off
title BKO Dashboard
echo Iniciando BKO Dashboard...
echo.
echo Acesse no navegador: http://localhost:8501
echo Para encerrar pressione CTRL+C nesta janela.
echo.
C:\bko_env\Scripts\streamlit run "%~dp0dashboard_bko.py" --server.headless false
pause

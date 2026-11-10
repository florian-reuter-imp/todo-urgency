@echo off
rem Startet das Todo-Tool in einem rahmenlosen App-Fenster.
cd /d "%~dp0"
start "" pythonw -m app

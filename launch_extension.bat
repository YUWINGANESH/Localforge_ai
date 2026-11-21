@echo off
title Launch LocalForge-AI Extension
echo Compiling extension...
cd /d "c:\dev2\extension"
call npm.cmd run compile
echo Launching VS Code Extension Development Host...
code --extensionDevelopmentPath="c:\dev2\extension" "c:\dev2"

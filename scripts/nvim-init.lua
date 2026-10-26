-- The personal snapshot remains separate from the shared host configuration.
local python = (vim.env.VIRTUAL_ENV or '/opt/venv') .. '/bin/python'
assert(vim.fn.executable(python) == 1, 'VOC editor requires its workspace Python interpreter')
dofile(vim.fn.stdpath('config') .. '/init.lua')

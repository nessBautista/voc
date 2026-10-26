-- Standard Lazy overrides: keep the personal UI/keys, install only container tooling.
local python = (vim.env.VIRTUAL_ENV or '/opt/venv') .. '/bin/python'

vim.keymap.set('n', '<leader>tw', function()
  local filter = { bufnr = 0 }
  local enabled = vim.diagnostic.is_enabled(filter)
  vim.diagnostic.enable(not enabled, filter)
  vim.notify((enabled and 'Hidden' or 'Shown') .. ' diagnostics for current buffer')
end, { desc = '[T]oggle [W]arnings and diagnostics' })

return {
  {
    'iamcco/markdown-preview.nvim',
    -- Browser preview brings a legacy HTTP dependency tree; retain in-editor Markdown.
    enabled = false,
  },
  {
    'neovim/nvim-lspconfig',
    config = function()
      local capabilities = require('blink.cmp').get_lsp_capabilities()
      vim.lsp.config('basedpyright', {
        capabilities = capabilities,
        settings = {
          python = { pythonPath = python },
          basedpyright = {
            analysis = {
              inlayHints = {
                variableTypes = true,
                functionReturnTypes = true,
                callArgumentNames = false,
                genericTypes = false,
              },
            },
          },
        },
      })
      vim.lsp.config('ruff', { capabilities = capabilities })
      vim.lsp.config('lua_ls', {
        capabilities = capabilities,
        settings = { Lua = { diagnostics = { globals = { 'vim' } } } },
      })
      require('mason-tool-installer').setup {
        ensure_installed = { 'basedpyright', 'ruff', 'lua-language-server', 'stylua' },
      }
      require('mason-lspconfig').setup {
        automatic_enable = { 'basedpyright', 'ruff', 'lua_ls' },
      }
      vim.diagnostic.config {
        severity_sort = true,
        float = { border = 'rounded', source = 'if_many' },
        signs = true,
        underline = true,
        virtual_text = false,
        virtual_lines = false,
      }
      vim.api.nvim_create_autocmd('LspAttach', {
        group = vim.api.nvim_create_augroup('voc-container-lsp', { clear = true }),
        callback = function(event)
          local telescope = require('telescope.builtin')
          local maps = {
            grd = telescope.lsp_definitions,
            grD = vim.lsp.buf.declaration,
            grr = telescope.lsp_references,
            gri = telescope.lsp_implementations,
            grt = telescope.lsp_type_definitions,
            grn = vim.lsp.buf.rename,
            gO = telescope.lsp_document_symbols,
            gW = telescope.lsp_dynamic_workspace_symbols,
          }
          for key, action in pairs(maps) do
            vim.keymap.set('n', key, action, { buffer = event.buf })
          end
          vim.keymap.set({ 'n', 'x' }, 'gra', vim.lsp.buf.code_action, { buffer = event.buf })
          local client = vim.lsp.get_client_by_id(event.data.client_id)
          if client and client.name == 'basedpyright' then
            vim.lsp.inlay_hint.enable(true, { bufnr = event.buf })
            vim.keymap.set('n', '<leader>ti', function()
              local filter = { bufnr = event.buf }
              vim.lsp.inlay_hint.enable(not vim.lsp.inlay_hint.is_enabled(filter), filter)
            end, { buffer = event.buf, desc = 'Toggle inlay hints' })
          end
        end,
      })
    end,
  },
  {
    'mfussenegger/nvim-dap',
    config = function()
      local dap = require('dap')
      local dapui = require('dapui')
      dapui.setup {}
      require('nvim-dap-virtual-text').setup {}
      dap.listeners.after.event_initialized['voc-ui'] = dapui.open
      dap.listeners.before.event_terminated['voc-ui'] = dapui.close
      dap.listeners.before.event_exited['voc-ui'] = dapui.close
      vim.fn.sign_define('DapBreakpoint', { text = 'B', texthl = 'DiagnosticError' })
      vim.fn.sign_define('DapStopped', { text = '>', texthl = 'DiagnosticWarn', linehl = 'CursorLine' })
      -- debugpy is already installed with the workspace's locked Jupyter dependencies.
      -- No Mason Python environment or changes to /opt/venv are needed.
      require('dap-python').setup(python)
      require('dap-python').resolve_python = function()
        return python
      end
      for _, config in ipairs(dap.configurations.python or {}) do
        if config.request == 'launch' then
          config.pythonPath = python
        end
      end
      table.insert(dap.configurations.python, {
        type = 'python',
        request = 'launch',
        name = 'VOC: local dataset summary (read only)',
        module = 'voc.cli',
        args = { 'summary', '--source', 'personal' },
        cwd = '/workspace',
        pythonPath = python,
        console = 'integratedTerminal',
        justMyCode = true,
      })
      table.insert(dap.configurations.python, {
        type = 'python',
        request = 'launch',
        name = 'VOC: dataset metadata (read only)',
        module = 'voc.cli',
        args = { 'dataset-info', '--source', 'personal' },
        cwd = '/workspace',
        pythonPath = python,
        console = 'integratedTerminal',
        justMyCode = true,
      })
    end,
  },
}

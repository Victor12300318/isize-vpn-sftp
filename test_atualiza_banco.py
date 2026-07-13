import unittest
from unittest.mock import patch, MagicMock
import urllib.parse
import json
import sys
import os

# Garante que o diretório atual está no path para importar o script de automação
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

# Importa o módulo que criamos para testar
import atualiza_banco

class TestAutomaçãoResiliencia(unittest.TestCase):

    @patch('atualiza_banco.WEBHOOK_N8N_ERROS', 'https://n8n-n8n.xjbony.easypanel.host/webhook/test')
    @patch('urllib.request.urlopen')
    def test_enviar_alerta_n8n_payload_formato(self, mock_urlopen):
        """Valida se o JSON de alerta enviado ao webhook do n8n possui o formato e chaves corretas."""
        # Configura o mock do urlopen para retornar sucesso (HTTP 200)
        mock_response = MagicMock()
        mock_response.status = 200
        mock_urlopen.return_value.__enter__.return_value = mock_response

        mensagem_erro = "Teste de falha crítica"
        detalhes_erro = "Exception: Falha simulada em ambiente de testes"

        # Dispara o alerta
        atualiza_banco.enviar_alerta_n8n(mensagem_erro, detalhes_erro)

        # Captura os argumentos com os quais o urllib.request.urlopen foi chamado
        self.assertTrue(mock_urlopen.called)
        req_arg = mock_urlopen.call_args[0][0]  # Obtém o objeto urllib.request.Request enviado
        
        # Lê o payload enviado na requisição
        payload_enviado = json.loads(req_arg.data.decode('utf-8'))

        # Validações da estrutura de dados exigida pelo n8n
        self.assertEqual(payload_enviado["status"], "erro")
        self.assertEqual(payload_enviado["script"], "atualiza_banco.py")
        self.assertEqual(payload_enviado["mensagem"], mensagem_erro)
        self.assertEqual(payload_enviado["detalhes"], detalhes_erro)
        self.assertIn("timestamp", payload_enviado)
        print("✅ Teste de Formato do Webhook n8n passou com sucesso!")

    @patch('socks.socksocket')
    @patch('atualiza_banco.enviar_alerta_n8n')
    @patch('atualiza_banco.PROXY_HABILITADO', True)
    def test_falha_conexao_vpn_proxy_socks5(self, mock_enviar_alerta, mock_socksocket):
        """Verifica se uma falha ao conectar ao Proxy SOCKS5 (VPN desconectada) envia o alerta correspondente para o n8n."""
        # Configura o mock para levantar um erro ao tentar conectar no Proxy SOCKS5
        mock_sock_inst = MagicMock()
        mock_sock_inst.connect.side_effect = Exception("Connection refused - Proxy Offline (VPN Desconectada)")
        mock_socksocket.return_value = mock_sock_inst

        # Executa a conexão (que deve falhar)
        with self.assertRaises(Exception):
            atualiza_banco.conectar_sftp_via_proxy()

        # Verifica se o alerta do n8n foi disparado com o contexto do Proxy/VPN
        self.assertTrue(mock_enviar_alerta.called)
        args, kwargs = mock_enviar_alerta.call_args
        self.assertIn("Erro ao conectar ao Proxy SOCKS5", args[0])
        self.assertIn("Connection refused - Proxy Offline", args[1])
        print("✅ Teste de Detecção de Falha de VPN/Proxy passou com sucesso!")

    @patch('socks.socksocket')
    @patch('paramiko.SSHClient')
    @patch('atualiza_banco.enviar_alerta_n8n')
    @patch('atualiza_banco.PROXY_HABILITADO', True)
    def test_falha_sftp_parado_ou_autenticacao(self, mock_enviar_alerta, mock_ssh_client, mock_socksocket):
        """Verifica se uma falha no servidor SFTP (SFTP parado ou queda) envia o alerta correspondente para o n8n."""
        # Configura o socket proxy para fingir conexão bem-sucedida
        mock_sock_inst = MagicMock()
        mock_socksocket.return_value = mock_sock_inst

        # Configura o cliente paramiko para levantar erro de SSH ao tentar autenticar/conectar
        mock_ssh_inst = MagicMock()
        mock_ssh_inst.connect.side_effect = Exception("SSH Connection timeout - SFTP fora do ar")
        mock_ssh_client.return_value = mock_ssh_inst

        # Executa a conexão sftp (que deve falhar no SSH)
        with self.assertRaises(Exception):
            atualiza_banco.conectar_sftp_via_proxy()

        # Verifica se o alerta foi disparado com a mensagem de falha do SFTP
        self.assertTrue(mock_enviar_alerta.called)
        args, _ = mock_enviar_alerta.call_args
        self.assertIn("Erro de autenticação ou conexão SFTP", args[0])
        self.assertIn("SSH Connection timeout - SFTP fora do ar", args[1])
        print("✅ Teste de Detecção de Queda de SFTP passou com sucesso!")

    @patch('pandas.read_excel')
    @patch('atualiza_banco.obter_colunas_reais_do_banco')
    @patch('atualiza_banco.enviar_alerta_n8n')
    def test_falha_banco_de_dados_nao_responde(self, mock_enviar_alerta, mock_obter_colunas, mock_read_excel):
        """Verifica se uma falha de conexão/resposta do banco de dados dispara o alerta de erro corretamente no n8n."""
        # Configura o pandas para ler um dataframe válido com a coluna chave "Proposta_iSize"
        import pandas as pd
        mock_df = pd.DataFrame({"Proposta_iSize": ["123", "456"], "coluna1": ["A", "B"]})
        mock_read_excel.return_value = mock_df

        # Configura o obter_colunas_reais_do_banco para lançar erro de banco fora do ar
        mock_obter_colunas.side_effect = Exception("PostgreSQL server closed the connection unexpectedly (Banco Inativo)")

        # Executa o processamento do arquivo simulado
        resultado = atualiza_banco.processar_arquivo_excel("caminho/fake.xlsx", "fake.xlsx")

        # Garante que o processamento retornou False (falhou)
        self.assertFalse(resultado)

        # Garante que o alerta com os detalhes da falha do Postgres foi enviado
        self.assertTrue(mock_enviar_alerta.called)
        args, _ = mock_enviar_alerta.call_args
        self.assertIn("Erro no processamento do Excel / Banco de Dados", args[0])
        self.assertIn("PostgreSQL server closed the connection unexpectedly", args[1])
        print("✅ Teste de Detecção de Queda de Banco de Dados passou com sucesso!")

if __name__ == '__main__':
    unittest.main()

# Dashboard Operacional ETE — com Análise Microbiológica por Vídeo

## O que foi adicionado

### 🔬 Seção de Microbiologia (ao final do dashboard)

O operador pode subir um vídeo filmado pelo microscópio diretamente no dashboard.
O sistema:
1. Extrai automaticamente 8 frames distribuídos ao longo do vídeo
2. Envia para a IA Claude analisar as imagens
3. Identifica os microrganismos presentes conforme a **Norma Técnica CETESB L1.025**
4. Gera diagnóstico do processo com semáforo (verde/laranja/vermelho)
5. Cruza com os parâmetros do último registro do Google Forms (pH, OD, SST, DQO)
6. Gera texto pronto para copiar no WhatsApp/relatório

---

## Configuração da chave API (necessária para a IA funcionar)

### No Streamlit Cloud:
1. Acesse o painel do seu app
2. Vá em **Settings → Secrets**
3. Adicione:
```toml
ANTHROPIC_API_KEY = "sk-ant-api03-SUA-CHAVE-AQUI"
```

### Localmente:
Crie o arquivo `.streamlit/secrets.toml` com o conteúdo acima.

Obtenha sua chave em: https://console.anthropic.com/settings/keys

---

## Alternativa sem ffmpeg

Se o ffmpeg não estiver disponível (Streamlit Cloud inclui por padrão),
use a opção de upload de imagens JPG/PNG diretamente na seção de microbiologia.

---

## Formatos de vídeo suportados
- MP4, MOV, AVI, WebM, MKV

## Dicas para melhor resultado
- Boa iluminação e foco no microscópio
- Vídeos de 10–60 segundos são suficientes
- Use aumento de 100–200x conforme recomendado pela CETESB

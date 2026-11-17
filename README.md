# Monitor de fundos imobiliários

Ranking dos FIIs e mapa dos imóveis, atualizados sozinhos no segundo sábado de cada mês com dados públicos:

- **CVM, dados abertos**: informes mensais e trimestrais dos FIIs (patrimônio, cotistas, imóveis, vacância, inadimplência, receita de aluguel, dividendos declarados).
- **Cotações e dividendos**: mFinance; Investidor10 e Yahoo Finance para os fundos que o mFinance não cobre.

> **Aviso.** Material de uso pessoal, criado com auxílio de inteligência artificial a partir de dados públicos. As informações, os cálculos e as classificações não foram validados por pessoa nem por profissional habilitado e podem conter erros. Não é recomendação, relatório de análise (Resolução CVM nº 20/2021) nem consultoria de valores mobiliários (Resolução CVM nº 19/2021), e não deve ser usado como base para decisões de investimento. O proprietário não se responsabiliza pelas informações nem por perdas decorrentes do seu uso.

## Como colocar no ar (uma vez só)

1. Crie uma conta em github.com, se ainda não tiver.
2. Crie um repositório novo (botão **New**). Para o site sair de graça pelo GitHub Pages, o repositório precisa ser **público**. Deixe-o vazio.
3. Na página do repositório, clique em **uploading an existing file** e arraste todo o conteúdo desta pasta (pastas `pipeline`, `site`, `.github` e os arquivos soltos). Confirme com **Commit changes**.
   - Se a pasta `.github` não subir (alguns sistemas escondem pastas que começam com ponto), use **Add file → Create new file**, digite o nome `.github/workflows/atualizar.yml` e cole o conteúdo do arquivo de mesmo nome.
4. Em **Settings → Pages**, em *Build and deployment*, escolha **Source: GitHub Actions**.
5. Em **Settings → Actions → General**, em *Workflow permissions*, marque **Read and write permissions** e salve.
6. Em **Actions → Atualizar dados**, clique em **Run workflow** para a primeira atualização (leva de 3 a 6 minutos).
7. O site fica em `https://SEU-USUARIO.github.io/NOME-DO-REPOSITORIO/`, com as páginas `ranking/` e `mapa/`.

Depois disso não há mais nada a fazer: o GitHub roda a atualização todo segundo sábado do mês, às 08h37 de Brasília, sem custo e sem uso do Claude. Se uma rodada falhar, o GitHub manda e-mail e o site continua com os dados anteriores.

## O que fica onde

| Caminho | Conteúdo |
| --- | --- |
| `pipeline/build_data.py` | Baixa os dados, calcula indicadores e a nota, gera `site/data/*.json`. Só usa a biblioteca padrão do Python. |
| `pipeline/refs.json` | Referências de aluguel de mercado (R$/m²/mês). Revise a cada trimestre com os relatórios das consultorias. |
| `pipeline/mapa_fundos.json` | Fundos que sempre aparecem no mapa (além dos fundos de tijolo mais bem colocados). |
| `pipeline/precos_complementares.json` | Cotações digitadas à mão para fundos sem fonte automática (valem 45 dias). |
| `site/ranking/`, `site/mapa/` | As páginas. Leem os arquivos de `site/data/`. |
| `site/geo/` | Contornos dos estados e dos municípios de SP e RJ. |
| `.github/workflows/atualizar.yml` | O agendamento. |

Para rodar no seu computador: `python3 pipeline/build_data.py --out site/data` e depois `python3 -m http.server -d site` (abra http://localhost:8000).

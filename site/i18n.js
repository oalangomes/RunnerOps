const translations = {
  en: {
    title: 'RunnerOps — GitHub Actions self-hosted runner operations',
    description: 'RunnerOps is a lightweight Linux operations layer for GitHub Actions self-hosted runners. runnerctl provides systemd-first, on-demand operations, CI feedback and portable Agent Skills.',
    skip: 'Skip to content',
    navAria: 'Primary navigation',
    languageAria: 'Language',
    navWhy: 'Why',
    navArchitecture: 'Architecture',
    navEvidence: 'Evidence',
    heroTitle: 'Operate self-hosted runners.<br /><span>Without babysitting them.</span>',
    heroLede: 'RunnerOps is a lightweight Linux operations layer for GitHub Actions self-hosted runners. Its public CLI, <code>runnerctl</code>, keeps lifecycle, machine-local state, diagnostics and CI feedback behind one stable interface.',
    viewGithub: 'View on GitHub',
    quickStartAria: 'Quick install',
    quickStart: 'quick start',
    statusAria: 'RunnerOps operational model',
    healthy: 'healthy',
    idle: 'idle',
    active: 'active',
    bootDisabled: 'boot: disabled',
    policyOnDemand: 'policy: on-demand',
    whyEyebrow: 'Why RunnerOps',
    whyTitle: 'Self-hosted runners are infrastructure.<br />Treat them like it.',
    whyBody: 'A handful of local runners quickly becomes a mix of processes, paths, labels, stale state and manual recovery. RunnerOps turns that collection into a small, inspectable operational platform.',
    feature1Title: 'On-demand by default',
    feature1Body: 'Runners stay installed but do not need to burn resources while idle. Start only the capacity a repository needs.',
    feature2Title: 'systemd is the authority',
    feature2Body: 'Lifecycle is delegated to the Linux service manager instead of growing another custom process supervisor.',
    feature3Title: 'Machine state stays local',
    feature3Body: 'XDG configuration, data, cache and runtime state stay outside the Git checkout and outside your public repository.',
    feature4Title: 'CI feedback for humans and agents',
    feature4Body: 'Correlate GitHub Actions by repository + SHA or PR, keep CI failures distinct from runner infrastructure failures, and emit structured JSON.',
    architectureEyebrow: 'Architecture',
    architectureTitle: 'A thin control plane over native Linux primitives.',
    architectureBody: 'RunnerOps keeps the public boundary small. The CLI orchestrates internal scripts; systemd remains responsible for service lifecycle.',
    architectureAria: 'Human or coding agent calls runnerctl, which invokes internal scripts and systemd services for GitHub Actions runners.',
    archOperator: 'Human / coding agent',
    archOperatorSmall: 'operator',
    archBoundary: 'stable CLI boundary',
    archScripts: 'internal scripts',
    archImplementation: 'implementation',
    archLifecycle: 'lifecycle authority',
    principleApi: 'Public API',
    principleBoot: 'Boot policy',
    principleState: 'State model',
    principleStateValue: 'XDG / machine-local',
    principleHost: 'Supported host',
    evidenceEyebrow: 'Engineering evidence',
    evidenceTitle: 'Not just a README claim.',
    evidenceBody: 'RunnerOps is developed as a small open-source engineering product: contracts, CI, release gates and real environment smoke tests are part of the artifact.',
    evidence1Title: 'Contract tests',
    evidence1Body: 'CLI versioning, repository routing, lifecycle boundaries, package integrity, CI watch behavior and Agent Skills are covered by focused contracts.',
    evidence2Title: 'Real GitHub API smoke',
    evidence2Body: 'Pushes to master exercise the CI feedback bridge against GitHub Actions rather than relying only on mocks.',
    evidence3Title: 'WSL2 + systemd smoke',
    evidence3Body: 'Release validation has exercised installation, on-demand lifecycle, repository-scoped operations and real self-hosted workflow execution.',
    evidence4Title: 'Immutable releases',
    evidence4Body: 'Versioned release gates, changelog discipline and post-release installation checks keep published tags as evidence instead of moving targets.',
    skillsTitle: 'The same operational boundary for humans and coding agents.',
    skillsBody: 'Portable Agent Skills let Codex, GitHub Copilot CLI, Claude Code and compatible clients operate runners and consume CI feedback without embedding machine inventory in prompts.',
    ctaTitle: 'Small enough to understand.<br />Useful enough to run for real.',
    exploreRepo: 'Explore the repository',
    readDocs: 'Read the docs',
    builtBy: 'Built by',
  },
  'pt-BR': {
    title: 'RunnerOps — operação de runners self-hosted do GitHub Actions',
    description: 'RunnerOps é uma camada operacional leve em Linux para runners self-hosted do GitHub Actions. A CLI runnerctl oferece operação on-demand, systemd, feedback de CI e Agent Skills portáveis.',
    skip: 'Ir para o conteúdo',
    navAria: 'Navegação principal',
    languageAria: 'Idioma',
    navWhy: 'Por quê',
    navArchitecture: 'Arquitetura',
    navEvidence: 'Evidências',
    heroTitle: 'Opere runners self-hosted.<br /><span>Sem ficar cuidando deles o tempo todo.</span>',
    heroLede: 'RunnerOps é uma camada operacional leve em Linux para runners self-hosted do GitHub Actions. Sua CLI pública, <code>runnerctl</code>, mantém lifecycle, estado local da máquina, diagnósticos e feedback de CI atrás de uma interface estável.',
    viewGithub: 'Ver no GitHub',
    quickStartAria: 'Instalação rápida',
    quickStart: 'início rápido',
    statusAria: 'Modelo operacional do RunnerOps',
    healthy: 'saudável',
    idle: 'ocioso',
    active: 'ativo',
    bootDisabled: 'boot: desabilitado',
    policyOnDemand: 'política: on-demand',
    whyEyebrow: 'Por que RunnerOps',
    whyTitle: 'Runners self-hosted são infraestrutura.<br />Trate-os como tal.',
    whyBody: 'Alguns runners locais rapidamente viram uma mistura de processos, caminhos, labels, estado obsoleto e recuperação manual. RunnerOps transforma essa coleção em uma pequena plataforma operacional inspecionável.',
    feature1Title: 'On-demand por padrão',
    feature1Body: 'Os runners permanecem instalados sem consumir recursos enquanto estão ociosos. Inicie apenas a capacidade que um repositório precisa.',
    feature2Title: 'systemd é a autoridade',
    feature2Body: 'O lifecycle é delegado ao gerenciador de serviços do Linux, em vez de criar mais um supervisor de processos customizado.',
    feature3Title: 'O estado da máquina fica local',
    feature3Body: 'Configuração XDG, dados, cache e estado de runtime ficam fora do checkout Git e fora do seu repositório público.',
    feature4Title: 'Feedback de CI para pessoas e agentes',
    feature4Body: 'Correlacione GitHub Actions por repositório + SHA ou PR, separe falhas de CI de falhas da infraestrutura dos runners e emita JSON estruturado.',
    architectureEyebrow: 'Arquitetura',
    architectureTitle: 'Um control plane fino sobre primitivas nativas do Linux.',
    architectureBody: 'RunnerOps mantém pequeno o boundary público. A CLI orquestra scripts internos; systemd continua responsável pelo lifecycle dos serviços.',
    architectureAria: 'Pessoa ou coding agent chama runnerctl, que aciona scripts internos e serviços systemd para runners do GitHub Actions.',
    archOperator: 'Pessoa / coding agent',
    archOperatorSmall: 'operador',
    archBoundary: 'boundary estável da CLI',
    archScripts: 'scripts internos',
    archImplementation: 'implementação',
    archLifecycle: 'autoridade de lifecycle',
    principleApi: 'API pública',
    principleBoot: 'Política de boot',
    principleState: 'Modelo de estado',
    principleStateValue: 'XDG / local da máquina',
    principleHost: 'Host suportado',
    evidenceEyebrow: 'Evidência de engenharia',
    evidenceTitle: 'Não é só uma promessa no README.',
    evidenceBody: 'RunnerOps é desenvolvido como um pequeno produto open source de engenharia: contratos, CI, gates de release e smokes em ambiente real fazem parte do artefato.',
    evidence1Title: 'Testes de contrato',
    evidence1Body: 'Versionamento da CLI, routing por repositório, boundaries de lifecycle, integridade de pacote, comportamento do CI watch e Agent Skills possuem contratos focados.',
    evidence2Title: 'Smoke com API real do GitHub',
    evidence2Body: 'Pushes na master exercitam o bridge de feedback de CI contra o GitHub Actions, em vez de depender apenas de mocks.',
    evidence3Title: 'Smoke WSL2 + systemd',
    evidence3Body: 'A validação de release já exercitou instalação, lifecycle on-demand, operações repo-scoped e execução real de workflow em runner self-hosted.',
    evidence4Title: 'Releases imutáveis',
    evidence4Body: 'Gates versionados, disciplina de changelog e checks pós-release mantêm tags publicadas como evidência, não como alvos móveis.',
    skillsTitle: 'O mesmo boundary operacional para pessoas e coding agents.',
    skillsBody: 'Agent Skills portáveis permitem que Codex, GitHub Copilot CLI, Claude Code e clientes compatíveis operem runners e consumam feedback de CI sem embutir inventário da máquina em prompts.',
    ctaTitle: 'Pequeno o bastante para entender.<br />Útil o bastante para rodar de verdade.',
    exploreRepo: 'Explorar o repositório',
    readDocs: 'Ler a documentação',
    builtBy: 'Criado por',
  },
};

const storageKey = 'runnerops-language';
const metaDescription = document.querySelector('meta[name="description"]');
const ogDescription = document.querySelector('meta[property="og:description"]');

function resolveInitialLanguage() {
  const saved = localStorage.getItem(storageKey);
  if (saved === 'en' || saved === 'pt-BR') return saved;
  return navigator.language.toLowerCase().startsWith('pt') ? 'pt-BR' : 'en';
}

function applyLanguage(language) {
  const dictionary = translations[language] ?? translations.en;

  document.documentElement.lang = language;
  document.title = dictionary.title;
  if (metaDescription) metaDescription.setAttribute('content', dictionary.description);
  if (ogDescription) ogDescription.setAttribute('content', dictionary.description);

  document.querySelectorAll('[data-i18n]').forEach((element) => {
    const key = element.dataset.i18n;
    if (dictionary[key]) element.textContent = dictionary[key];
  });

  document.querySelectorAll('[data-i18n-html]').forEach((element) => {
    const key = element.dataset.i18nHtml;
    if (dictionary[key]) element.innerHTML = dictionary[key];
  });

  document.querySelectorAll('[data-i18n-aria]').forEach((element) => {
    const key = element.dataset.i18nAria;
    if (dictionary[key]) element.setAttribute('aria-label', dictionary[key]);
  });

  document.querySelectorAll('[data-lang]').forEach((button) => {
    const isActive = button.dataset.lang === language;
    button.classList.toggle('active', isActive);
    button.setAttribute('aria-pressed', String(isActive));
  });

  localStorage.setItem(storageKey, language);
}

document.querySelectorAll('[data-lang]').forEach((button) => {
  button.addEventListener('click', () => applyLanguage(button.dataset.lang));
});

applyLanguage(resolveInitialLanguage());

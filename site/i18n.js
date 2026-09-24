const translations = {
  en: {
    title: 'RunnerOps — GitHub Actions self-hosted runner operations',
    description: 'RunnerOps is a lightweight Linux operations layer for GitHub Actions self-hosted runners and a public engineering lab for CI, local compute, automation and AI-assisted operations.',
    skip: 'Skip to content',
    navAria: 'Primary navigation',
    languageAria: 'Language',
    navWhy: 'Why',
    navLab: 'Lab',
    navArchitecture: 'Architecture',
    navEvidence: 'Evidence',
    heroTitle: 'Operate self-hosted runners.<br /><span>Without babysitting them.</span>',
    heroLede: 'RunnerOps is a lightweight Linux operations layer for GitHub Actions self-hosted runners. Its public CLI, <code>runnerctl</code>, keeps lifecycle, CI feedback, capacity evidence and governed local autoscaling behind one stable interface.',
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
    feature4Title: 'Evidence before automation',
    feature4Body: 'Observe queue and capacity, keep decisions deterministic, and persist enough evidence to explain why RunnerOps acted — or refused to act.',
    labEyebrow: 'Engineering lab',
    labTitle: 'A working tool.<br /><span>And a place to learn in public.</span>',
    labBody: 'RunnerOps is also a public engineering lab for exploring CI infrastructure, local compute, automation and AI-assisted operations in a real system. Experiments stay experiments until evidence justifies turning them into product capabilities.',
    lab1Title: 'DevOps & CI performance',
    lab1Body: 'Study queueing, concurrency, capacity, CI critical paths, failure modes and the trade-offs behind self-hosted infrastructure.',
    lab2Title: 'Local compute economics',
    lab2Body: 'Measure existing Linux hardware as infrastructure: utilization, throughput, energy, cost and when hosted compute is still the better answer.',
    lab3Title: 'Agents & AI tooling',
    lab3Body: 'Explore Agent Skills, bounded tool use and evidence-grounded generative AI without making an LLM the hidden scaling policy.',
    lab4Title: 'Build, measure, explain',
    lab4Body: 'Originality is optional. Reproducible experiments, explicit trade-offs, tests, failures, corrections and useful documentation are not.',
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
    evidence3Title: 'Real-host qualification',
    evidence3Body: 'v0.4.0 proved bounded local provisioning, recovery without duplicate registration, exact activation and a real GitHub Actions workload on the newly provisioned runner.',
    evidence4Title: 'Immutable releases',
    evidence4Body: 'Versioned release gates, changelog discipline and post-release installation checks keep published tags as evidence instead of moving targets.',
    skillsTitle: 'The same operational boundary for humans and coding agents.',
    skillsBody: 'Portable Agent Skills let Codex, GitHub Copilot CLI, Claude Code and compatible clients operate runners and consume CI feedback without embedding machine inventory in prompts.',
    ctaTitle: 'Build it. Measure it.<br />Run it for real.',
    exploreRepo: 'Explore the repository',
    readDocs: 'Read the docs',
    builtBy: 'Built by',
  },
  'pt-BR': {
    title: 'RunnerOps — operação de runners self-hosted do GitHub Actions',
    description: 'RunnerOps é uma camada operacional leve em Linux para runners self-hosted do GitHub Actions e um laboratório público de engenharia para CI, compute local, automação e operações assistidas por IA.',
    skip: 'Ir para o conteúdo',
    navAria: 'Navegação principal',
    languageAria: 'Idioma',
    navWhy: 'Por quê',
    navLab: 'Lab',
    navArchitecture: 'Arquitetura',
    navEvidence: 'Evidências',
    heroTitle: 'Opere runners self-hosted.<br /><span>Sem ficar cuidando deles o tempo todo.</span>',
    heroLede: 'RunnerOps é uma camada operacional leve em Linux para runners self-hosted do GitHub Actions. Sua CLI pública, <code>runnerctl</code>, mantém lifecycle, feedback de CI, evidência de capacidade e autoscaling local governado atrás de uma interface estável.',
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
    feature4Title: 'Evidência antes da automação',
    feature4Body: 'Observe fila e capacidade, mantenha decisões determinísticas e persista evidência suficiente para explicar por que o RunnerOps agiu — ou recusou agir.',
    labEyebrow: 'Laboratório de engenharia',
    labTitle: 'Uma ferramenta que funciona.<br /><span>E um lugar para aprender em público.</span>',
    labBody: 'RunnerOps também é um laboratório público de engenharia para explorar infraestrutura de CI, compute local, automação e operações assistidas por IA em um sistema real. Experimentos continuam sendo experimentos até que evidência justifique transformá-los em capacidades do produto.',
    lab1Title: 'DevOps & performance de CI',
    lab1Body: 'Estudar filas, concorrência, capacidade, caminho crítico de CI, modos de falha e os trade-offs por trás de infraestrutura self-hosted.',
    lab2Title: 'Economia de compute local',
    lab2Body: 'Medir hardware Linux existente como infraestrutura: utilização, throughput, energia, custo e quando compute hospedado ainda é a melhor resposta.',
    lab3Title: 'Agentes & AI tooling',
    lab3Body: 'Explorar Agent Skills, tool use limitado e IA generativa baseada em evidência sem transformar um LLM na policy escondida de autoscaling.',
    lab4Title: 'Construir, medir, explicar',
    lab4Body: 'Originalidade é opcional. Experimentos reproduzíveis, trade-offs explícitos, testes, falhas, correções e documentação útil não são.',
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
    evidence3Title: 'Qualificação em host real',
    evidence3Body: 'A v0.4.0 comprovou provisioning local limitado, recuperação sem registro duplicado, ativação exata e workload real do GitHub Actions no runner recém-provisionado.',
    evidence4Title: 'Releases imutáveis',
    evidence4Body: 'Gates versionados, disciplina de changelog e checks pós-release mantêm tags publicadas como evidência, não como alvos móveis.',
    skillsTitle: 'O mesmo boundary operacional para pessoas e coding agents.',
    skillsBody: 'Agent Skills portáveis permitem que Codex, GitHub Copilot CLI, Claude Code e clientes compatíveis operem runners e consumam feedback de CI sem embutir inventário da máquina em prompts.',
    ctaTitle: 'Construa. Meça.<br />Rode de verdade.',
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

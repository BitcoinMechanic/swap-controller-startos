import defaultDict, { LangDict } from './default'

// Keep new status strings readable until each locale has a translation.
const fallback = Object.fromEntries(Object.entries(defaultDict).map(([text, id]) => [id, text])) as LangDict

export default {
  es_ES: {
    ...fallback,
    0: '¡Iniciando Swap Controller!',
    1: 'Interfaz web',
    2: 'La interfaz web está lista',
    3: 'La interfaz web no está lista',
  },
  de_DE: {
    ...fallback,
    0: 'Starte Swap Controller!',
    1: 'Weboberfläche',
    2: 'Die Weboberfläche ist bereit',
    3: 'Die Weboberfläche ist nicht bereit',
  },
  pl_PL: {
    ...fallback,
    0: 'Uruchamianie Swap Controller!',
    1: 'Interfejs webowy',
    2: 'Interfejs webowy jest gotowy',
    3: 'Interfejs webowy nie jest gotowy',
  },
  fr_FR: {
    ...fallback,
    0: 'Démarrage de Swap Controller !',
    1: 'Interface web',
    2: "L'interface web est prête",
    3: "L'interface web n'est pas prête",
  },
} satisfies Record<string, LangDict>

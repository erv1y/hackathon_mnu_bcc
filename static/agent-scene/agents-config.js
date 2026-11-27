// Персонажи сцены. Ключи совпадают с агентами бэкенда (profiles/*.json → agents).
// Порядок — визуальная цепочка передачи документа в глубокой проверке.
export const PIPELINE_ORDER = ["payments", "kyc_aml", "security", "logic", "assembler"];

export function agentConfigs(p) {
  return [
    {
      key: "payments", name: "sceneNamePayments", role: "sceneRolePayments", desk: "payments",
      outfit: { top: p.sky, shirt: p.white, pants: p.dark, tie: p.accent, hair: 1, skin: 0, style: "blazer", hairStyle: "bun" },
    },
    {
      key: "kyc_aml", name: "sceneNameKyc", role: "sceneRoleKyc", desk: "kyc_aml",
      outfit: { top: p.mint, shirt: p.white, pants: p.woodDark, hair: 0, skin: 1, style: "cardigan", hairStyle: "short" },
    },
    {
      key: "security", name: "sceneNameSecurity", role: "sceneRoleSecurity", desk: "security",
      outfit: { top: p.heroLight, shirt: p.soft, pants: p.dark, hair: 2, skin: 2, style: "blazer", hairStyle: "bob", badge: true },
    },
    {
      key: "logic", name: "sceneNameLogic", role: "sceneRoleLogic", desk: "logic",
      outfit: { top: p.peach, shirt: p.white, pants: p.sky, hair: 3, skin: 3, style: "sweater", hairStyle: "curly", glasses: true },
    },
  ];
}

export function quickAgentConfig(p) {
  return {
    key: "quick", name: "sceneNameQuick", role: "sceneRoleQuick",
    outfit: { top: p.soft, shirt: p.soft, pants: p.sky, hair: 2, skin: 0, style: "hoodie", hairStyle: "short", glowBadge: true },
  };
}

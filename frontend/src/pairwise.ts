/**
 * Regras da comparação par a par no cliente.
 *
 * Aqui ficam apenas a **máquina de estados da interação** (o que acontece com a
 * intensidade quando a dimensão preferida muda) e a validação de completude que
 * decide se o formulário pode ser enviado. A conversão para a escala de Saaty e
 * o valor da matriz continuam sendo do backend (`backend/app/pairwise.py`) — o
 * payload não carrega número nenhum, só preferência e intensidade.
 *
 * A tabela de intensidades aparece abaixo somente para a legenda do modo de
 * desenvolvimento; nada nesta camada alimenta o cálculo.
 */
import type { DimensionDef, PairwiseAnswer, PairwiseIntensity } from "./types";

export const EQUAL = "equal";

export const INTENSITIES: { id: PairwiseIntensity; label: string; saaty: number }[] = [
  { id: "moderate", label: "Moderadamente", saaty: 3 },
  { id: "strong", label: "Fortemente", saaty: 5 },
  { id: "very_strong", label: "Muito fortemente", saaty: 7 },
  { id: "extreme", label: "Extremamente", saaty: 9 },
];

/** Uma comparação só está completa com "igual importância" ou com dimensão + intensidade. */
export function isComplete(value: PairwiseAnswer | undefined | null): boolean {
  if (!value) return false;
  if (value.preference === EQUAL) return value.intensity === null;
  return (
    (value.preference === value.left || value.preference === value.right) &&
    value.intensity !== null
  );
}

/**
 * Nova resposta ao escolher a dimensão prioritária (ou a indiferença).
 *
 * A intensidade anterior é **descartada** em qualquer troca de preferência: em
 * "igual importância" ela não existe (a razão já é 1) e, ao trocar de dimensão,
 * mantê-la seria assumir que o gestor quis a mesma força na direção oposta —
 * julgamento que ele não deu. Ele reinforma, num clique.
 */
export function selectPreference(
  current: PairwiseAnswer,
  preference: string,
): PairwiseAnswer {
  if (current.preference === preference) return current;
  return { ...current, preference, intensity: null };
}

/** Intensidade só faz sentido com uma dimensão preferida — na indiferença é ignorada. */
export function selectIntensity(
  current: PairwiseAnswer,
  intensity: PairwiseIntensity,
): PairwiseAnswer {
  if (current.preference === EQUAL) return current;
  return { ...current, intensity };
}

/** Estado inicial de uma comparação ainda não respondida. */
export function emptyAnswer(left: string, right: string): PairwiseAnswer {
  return { left, right, preference: "", intensity: null };
}

/** Rótulo e ícone de uma dimensão, com o próprio id como último recurso. */
export function dimensionOf(
  dimensions: Record<string, DimensionDef> | undefined,
  id: string,
): { id: string; label: string; icon?: string } {
  const d = dimensions?.[id];
  return { id, label: d?.label || id, icon: d?.icon };
}

/**
 * Which planner a Director pass runs, and how the passes that end early land.
 *
 * Podcast and viral video are first-class skills the planner layer has always
 * supported, but the store used to hardcode the music-video planner for every
 * workflow: a podcast planned that way loses the conversation structure its own
 * planner exists to build, and `skill_map.get(pipeline_type, "music_video")` on
 * the server fails silently, so nothing said so.
 *
 * Ours, so it lives here rather than in the store upstream edits every release.
 */

import type { DirectorSkill, ShortFilmPath } from '../types'

export type DirectorPipelineType =
  | 'music_video'
  | 'short_film_audio'
  | 'short_film_story'
  | 'podcast'
  | 'viral_video'

/** The skill the planner layer should run for the skill the user picked. */
export function directorPlannerSkill(skill: DirectorSkill | null | undefined): DirectorSkill {
  return skill === 'podcast' || skill === 'viral_video' ? skill : 'music_video'
}

/** The pipeline a pass writes, from the skill and the short-film path. */
export function directorPipelineType(
  skill: DirectorSkill | null | undefined,
  shortFilmPath: ShortFilmPath | null | undefined,
): DirectorPipelineType {
  if (skill === 'podcast') return 'podcast'
  if (skill === 'viral_video') return 'viral_video'
  if (shortFilmPath === 'story') return 'short_film_story'
  if (shortFilmPath === 'audio') return 'short_film_audio'
  return 'music_video'
}

/**
 * The viral planner is concept-driven and clamps its length to the platform's
 * norm; both are defaults the user can tune afterwards.
 */
export function directorViralPlanOptions(
  skill: DirectorSkill,
  concept: string,
): Record<string, unknown> {
  if (skill !== 'viral_video') return {}
  return { concept, platform: 'general', style: 'cinematic' }
}

/**
 * Stop pressed on the planning card is a state, not a failure: the server
 * answers a cancelled pass with an empty plan, so bail out before that empty
 * result replaces the plans under review.
 */
export function clearDirectorPass(
  set: (patch: { directorLoading: boolean; directorError: null }) => void,
): void {
  set({ directorLoading: false, directorError: null })
}

/**
 * Why a Studio reroll cannot run, or null when it can.
 *
 * The settings load aborts without a word when the sidecar carries no params, and
 * for a Director clip it opens the Director project instead of applying Studio
 * settings. Firing a Studio generation anyway is what made the action look like
 * it did nothing, so say why instead of staying silent.
 */
export function studioRerollBlockedReason(
  meta: { director_pipeline_id?: string; params?: unknown } | null | undefined,
): string | null {
  if (meta?.director_pipeline_id) {
    return 'This clip belongs to a Director pipeline. Regenerate it from the clip '
      + 'actions or the Director Dashboard so it keeps its place in the film.'
  }
  if (!meta?.params) {
    return 'This file carries no saved settings, so there is nothing to regenerate.'
  }
  return null
}

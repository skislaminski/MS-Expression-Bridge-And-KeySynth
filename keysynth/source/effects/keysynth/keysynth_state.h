/* effects/keysynth/keysynth_state.h - per-instance state of the KeySynth kernel.
 *
 * Shared by the kernel and the desktop harness so both see one layout.
 * Lives at instance[2]; init zeroes it, so all-zero must be a valid idle state.
 *
 * Budget (manifest "state_bytes", limit 188 on the cleanroom scaffold):
 *   2 x 24  per oscillator: phase and last gain (kept across blocks), plus
 *           increment, reciprocal, gain target and waveform. Those four are
 *           rewritten every block; they sit here because the kernel has no
 *           stack and would otherwise run out of registers.
 *   1 x 4   envelope
 *   4 x 4   smoothed Level, Mix, vibrato depth, tremolo depth
 *   1 x 4   LFO phase
 *   2 x 4   pitch: where the voice is, and where the Key knob last pointed
 *   1 x 4   flags: gate, glide in progress
 *   ------
 *     84 bytes of 188
 */
#ifndef KEYSYNTH_STATE_H
#define KEYSYNTH_STATE_H

#include <stdint.h>

#define KS_STATE_BYTES 84

#define KS_FLAG_GATE   1     /* the Key knob was above 0 in the last block */
#define KS_FLAG_GLIDE  2     /* a slide between two notes is under way */

typedef struct {
    float   ph;      /* phase, 0..1 (kept across blocks) */
    float   g;       /* gain reached at the end of the last block (kept) */
    float   inc;     /* this block: phase increment per sample */
    float   rinc;    /* this block: 1 / inc */
    float   tg;      /* this block: gain target */
    int32_t wave;    /* this block: 0 saw, 1 square, 2 triangle, else sine */
} ks_osc_t;

typedef struct {
    ks_osc_t osc[2];
    float    env;    /* envelope, 0..1 */
    float    sLevel; /* smoothed Level amplitude */
    float    sMix;   /* smoothed Mix, 0..1 */
    float    sVib;   /* smoothed vibrato depth, 0..1 */
    float    sTrem;  /* smoothed tremolo depth, 0..1 */
    float    lph;    /* LFO phase, 0..1 */
    float    pit;    /* pitch of the voice as a MIDI note number, with
                      * fraction; held through the release */
    float    ptp;    /* pitch the Key knob asked for in the last gated block */
    int32_t  flags;  /* KS_FLAG_* */
} ks_state_t;

/* fails to compile if the layout and the manifest drift apart */
typedef char ks_state_size_check[(sizeof(ks_state_t) == KS_STATE_BYTES) ? 1 : -1];

#endif /* KEYSYNTH_STATE_H */

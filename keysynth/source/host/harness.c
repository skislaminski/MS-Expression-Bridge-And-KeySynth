/* host/harness.c - desktop test rig and WAV renderer for the KeySynth kernel.
 *
 * Runs the SAME kernel source that goes into the ZD2 (effects/keysynth/
 * keysynth.c), compiled with clang, against a stand-in for the pedal's
 * instance/ctx. Uses build/sh_params.h and the generated tables.
 *
 *   harness test              run the automatic tests, exit 0 when all pass
 *   harness render <dir>      render audition WAVs into <dir>
 *
 * Build and run through host/run_tests.sh (from the project root).
 */

#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "sh_params.h"
#include "ks_tables.h"
#include "keysynth_state.h"
#include "ks_manifest.h"

/* the tables the pedal build ships through const_blob */
const uint32_t KS_TAB[KS_TAB_WORDS] = {
#include "ks_tables.inc"
};

#define FS        44100.0
#define BLK       SH_FRAMES
#define AMP       0.25              /* KS_AMP in the kernel */
#define PI        3.14159265358979323846

enum { K_KEY, K_LEVEL, K_WAVE1, K_WAVE2, K_PITCH, K_DETUNE, K_OSCMIX,
       K_GLIDE, K_ATTACK, K_RELEASE, K_LFO, K_RATE, K_COUNT };
enum { W_SAW, W_SQR, W_TRI, W_SINE };            /* Wave1; Wave2 is +1, 0 = Off */

/* Key knob: 0 = gate off, 1..1000 = 10-cent steps above MIDI note 12 */
#define KEYOF(note)   (((note) - 12) * 10 + 1)
#define KEY_BASE      12.0
/* LFO knob: Vib50..Vib1, Off, Trm1..Trm50 */
#define LFO_OFF       50
#define LFO_VIB(d)    (50 - (d))
#define LFO_TRM(d)    (50 + (d))

static const char *const K_NAME[K_COUNT] = {
    "Key", "Level", "Wave1", "Wave2", "Pitch", "Dtune", "Mix",
    "Glide", "Atk", "Rel", "LFO", "Rate" };
static const int K_SLOT[K_COUNT] = {
    SH_PARAM_KEY, SH_PARAM_LEVEL, SH_PARAM_WAVE1, SH_PARAM_WAVE2,
    SH_PARAM_PITCH, SH_PARAM_DTUNE, SH_PARAM_MIX, SH_PARAM_GLIDE,
    SH_PARAM_ATK, SH_PARAM_REL, SH_PARAM_LFO, SH_PARAM_RATE };

/* ------------------------------------------------------------------ rig */

typedef struct {
    void    *instance[4];
    void    *ctx[16];
    float    coeff[16];
    uint32_t state[64];             /* instance[2]; guard word behind the state */
    float    bus[2 * BLK];
    int      knob[K_COUNT];
} rig_t;

static uint32_t rng_s = 0x1234abcdu;
static uint32_t rnd(void)
{
    rng_s ^= rng_s << 13; rng_s ^= rng_s >> 17; rng_s ^= rng_s << 5;
    return rng_s;
}
static float rndf(void) { return (float)(rnd() >> 8) * (1.0f / 16777216.0f); }
static float rnd_pm(float a) { return (2.0f * rndf() - 1.0f) * a; }

/* the generated edit handlers: coefficient = float(value) * float32(1/max);
 * a stock_vol knob maps through the stock VOL curve instead */
static float knob_coef(int k, int v)
{
    if (KS_KNOBS[k].vol)
        return (v <= 80) ? (float)v * (1.0f / 80.0f)
                         : 1.0f + (float)(v - 80) * (1.0f / 40.0f);
    return (float)v * (float)(1.0 / (double)KS_KNOBS[k].max);
}

static void rig_wire(rig_t *r)
{
    memset(r, 0, sizeof *r);
    r->instance[1] = r->coeff;
    r->instance[2] = r->state;
    r->ctx[SH_CTX_EFF] = r->bus;
}

static void set_knob(rig_t *r, int k, int v)
{
    r->knob[k] = v;
    r->coeff[K_SLOT[k]] = knob_coef(k, v);
}

static void set_fade(rig_t *r, float f)
{
    r->coeff[SH_COEFF_BYPASS] = f;
    r->coeff[1] = 1.0f - f;
}

/* what the generated init does: defaults in, state zeroed, guard written */
static void rig_init(rig_t *r)
{
    int k;
    rig_wire(r);
    for (k = 0; k < K_COUNT; k++) set_knob(r, k, KS_KNOBS[k].def);
    r->state[SH_STATE_GUARD_WORD] = SH_STATE_GUARD;
    set_fade(r, 1.0f);
}

static ks_state_t *st_of(rig_t *r) { return (ks_state_t *)r->state; }

/* run one block; dry may be NULL (silence). out receives 32 samples. */
static void run_block(rig_t *r, const float *dry, float *out)
{
    if (dry) memcpy(r->bus, dry, sizeof r->bus);
    else     memset(r->bus, 0, sizeof r->bus);
    SH_AUDIO_FN(r->instance, r->ctx);
    if (out) memcpy(out, r->bus, sizeof r->bus);
}

static void run_blocks(rig_t *r, int n)
{
    while (n-- > 0) run_block(r, NULL, NULL);
}

/* -------------------------------------------------------------- checks */

static int n_fail, n_check;
#define CHECK(cond, ...) do {                                          \
        n_check++;                                                     \
        if (!(cond)) {                                                 \
            if (n_fail < 40) { printf("    FAIL: "); printf(__VA_ARGS__); printf("\n"); } \
            n_fail++;                                                  \
        }                                                              \
    } while (0)

static int section_start;
static void begin(const char *name)
{
    printf("%s\n", name);
    section_start = n_fail;
}
static void end(void)
{
    printf("    %s\n", n_fail == section_start ? "ok" : "FAILED");
}

static double note_inc(double note)
{
    double inc = 440.0 * pow(2.0, (note - 69.0) / 12.0) / FS;
    return inc > 0.45 ? 0.45 : inc;
}

/* pitch of oscillator 1 this block, as a MIDI note number with fraction */
static double osc_pitch(rig_t *r)
{
    return 69.0 + 12.0 * log2((double)st_of(r)->osc[0].inc * FS / 440.0);
}

static int is_zero_block(const float *b)
{
    static const float z[2 * BLK];
    return memcmp(b, z, sizeof z) == 0;
}

/* ---- 1. uninitialised or insane input gives exact silence ------------- */
static void test_uninitialised(void)
{
    rig_t r;
    float dry[2 * BLK], out[2 * BLK];
    int t, i, k;

    begin("1. uninitialised state and insane coefficients give exact silence");

    for (t = 0; t < 500; t++) {             /* garbage everywhere, no guard */
        rig_wire(&r);
        for (i = 0; i < 64; i++) r.state[i] = rnd();
        if (r.state[SH_STATE_GUARD_WORD] == SH_STATE_GUARD)
            r.state[SH_STATE_GUARD_WORD] ^= 1u;
        for (i = 0; i < 16; i++) { uint32_t w = rnd(); memcpy(&r.coeff[i], &w, 4); }
        for (i = 0; i < 2 * BLK; i++) dry[i] = rnd_pm(1.0f);
        run_block(&r, dry, out);
        CHECK(is_zero_block(out), "garbage instance %d did not give silence", t);
    }

    rig_wire(&r);                           /* all-zero state, defaults, no guard */
    for (k = 0; k < K_COUNT; k++) set_knob(&r, k, KS_KNOBS[k].def);
    set_knob(&r, K_KEY, KEYOF(57));
    set_fade(&r, 1.0f);
    for (i = 0; i < 2 * BLK; i++) dry[i] = rnd_pm(1.0f);
    run_block(&r, dry, out);
    CHECK(is_zero_block(out), "sane coefficients without the guard word gave sound");

    {                                       /* guard ok, one coefficient insane */
        static const float bad[] = { NAN, INFINITY, -INFINITY, -0.5f, 2.0f, 1.0e30f };
        int slots[K_COUNT + 1], s, b;
        slots[0] = SH_COEFF_BYPASS;
        for (k = 0; k < K_COUNT; k++) slots[k + 1] = K_SLOT[k];
        for (s = 0; s < K_COUNT + 1; s++)
            for (b = 0; b < (int)(sizeof bad / sizeof bad[0]); b++) {
                rig_init(&r);
                set_knob(&r, K_KEY, KEYOF(57));
                run_blocks(&r, 50);
                r.coeff[slots[s]] = bad[b];
                for (i = 0; i < 2 * BLK; i++) dry[i] = rnd_pm(1.0f);
                run_block(&r, dry, out);
                CHECK(is_zero_block(out), "coeff[%d] = %g did not give silence",
                      slots[s], (double)bad[b]);
            }
    }
    end();
}

/* ---- 2. fuzz: random knob changes, never NaN/Inf, bounded ------------- */
static void test_fuzz(void)
{
    rig_t r;
    float dry[2 * BLK], out[2 * BLK];
    double peak = 0.0, peak_out = 0.0;
    long sounding = 0;
    int b, i, k;

    begin("2. 60000 blocks of random knob changes: finite and bounded");
    rig_init(&r);
    for (b = 0; b < 60000; b++) {
        if ((rnd() & 7) == 0) {
            k = (int)(rnd() % K_COUNT);
            set_knob(&r, k, (int)(rnd() % (unsigned)(KS_KNOBS[k].max + 1)));
        }
        if ((rnd() & 63) == 0) {
            unsigned c = rnd() % 3;
            set_fade(&r, c == 0 ? 0.0f : c == 1 ? 1.0f : rndf());
        }
        for (i = 0; i < 2 * BLK; i++) dry[i] = rnd_pm(0.7f);
        run_block(&r, dry, out);
        for (i = 0; i < 2 * BLK; i++) {
            double s = (double)out[i] - (double)dry[i];
            CHECK(isfinite(out[i]), "block %d sample %d not finite", b, i);
            CHECK(fabs(out[i]) < 8.0, "block %d sample %d = %g", b, i, (double)out[i]);
            if (fabs(s) > peak) peak = fabs(s);
            if (fabs(out[i]) > peak_out) peak_out = fabs(out[i]);
        }
        for (i = 0; i < BLK; i++) {         /* both channels get the same synth */
            double sa = (double)out[i] - (double)dry[i];
            double sb = (double)out[i + BLK] - (double)dry[i + BLK];
            CHECK(fabs(sa - sb) < 1.0e-6, "block %d: channels differ by %g", b, sa - sb);
        }
        {
            const ks_state_t *st = st_of(&r);
            CHECK(isfinite(st->env) && st->env >= 0.0f && st->env <= 1.0f, "env %g", (double)st->env);
            for (i = 0; i < 2; i++) {
                CHECK(st->osc[i].ph >= 0.0f && st->osc[i].ph < 1.0f, "phase %g", (double)st->osc[i].ph);
                CHECK(st->osc[i].g >= 0.0f && st->osc[i].g <= 0.4f, "gain %g", (double)st->osc[i].g);
            }
            if (st->osc[0].g > 0.0f || st->osc[1].g > 0.0f) sounding++;
        }
    }
    CHECK(peak <= 0.45, "synth peak %g exceeds 0.45", peak);
    CHECK(sounding > 20000, "fuzz barely made sound (%ld blocks)", sounding);
    printf("    synth peak %.4f, output peak %.4f, %ld of 60000 blocks sounding\n",
           peak, peak_out, sounding);

    /* a poisoned state word must not mute the voice or leak onto the bus */
    {
        static const float poison[] = { NAN, INFINITY, -INFINITY, 1.0e30f, -3.0f };
        int w, q, j;
        for (w = 0; w < KS_STATE_BYTES / 4; w++)
            for (q = 0; q < (int)(sizeof poison / sizeof poison[0]); q++) {
                rig_init(&r);
                set_knob(&r, K_KEY, KEYOF(57));
                run_blocks(&r, 400);
                memcpy(&r.state[w], &poison[q], 4);
                for (j = 0; j < 400; j++) {
                    run_block(&r, NULL, out);
                    for (i = 0; i < 2 * BLK; i++)
                        CHECK(isfinite(out[i]) && fabs(out[i]) <= 0.45,
                              "state word %d poisoned: out %g", w, (double)out[i]);
                }
                CHECK(st_of(&r)->osc[0].g > 0.05f,
                      "state word %d poisoned with %g: voice stayed mute", w, (double)poison[q]);
            }
    }

    /* garbage arriving on the bus is scrubbed while the synth writes */
    {
        static const float junk[] = { NAN, INFINITY, -INFINITY, 1.0e9f };
        int q;
        rig_init(&r);
        set_knob(&r, K_KEY, KEYOF(57));
        run_blocks(&r, 400);
        for (q = 0; q < 4; q++) {
            for (i = 0; i < 2 * BLK; i++) dry[i] = junk[q];
            run_block(&r, dry, out);
            for (i = 0; i < 2 * BLK; i++)
                CHECK(isfinite(out[i]) && fabs(out[i]) < 8.0, "bus junk %g came out as %g",
                      (double)junk[q], (double)out[i]);
        }
    }
    end();
}

/* frequency of channel A by upward zero crossings, over n blocks */
static double measure_hz(rig_t *r, int nblocks)
{
    float out[2 * BLK];
    double first = -1.0, last = -1.0, prev = 0.0;
    long count = 0, t = 0;
    int b, i;
    for (b = 0; b < nblocks; b++) {
        run_block(r, NULL, out);
        for (i = 0; i < BLK; i++, t++) {
            double v = out[i];
            if (prev < 0.0 && v >= 0.0) {
                double x = (double)(t - 1) + prev / (prev - v);
                if (first < 0.0) first = x;
                last = x;
                count++;
            }
            prev = v;
        }
    }
    return count < 2 ? 0.0 : (double)(count - 1) * FS / (last - first);
}

/* ---- 3. every Key value ---------------------------------------------- */
static void test_all_keys(void)
{
    rig_t r;
    float out[2 * BLK];
    double worst = 0.0;
    int key, b, i;

    begin("3. all 1001 Key values: pitch in 10-cent steps, level");
    for (key = 0; key <= 1000; key++) {
        rig_init(&r);
        set_knob(&r, K_WAVE1, W_SINE);
        set_knob(&r, K_WAVE2, 0);
        set_knob(&r, K_ATTACK, 0);
        set_knob(&r, K_KEY, key);
        if (key == 0) {
            int silent = 1;
            for (b = 0; b < 400; b++) {
                run_block(&r, NULL, out);
                if (!is_zero_block(out)) silent = 0;
            }
            CHECK(silent, "Key 0 made sound");
            continue;
        }
        run_blocks(&r, 600);
        {
            ks_state_t *st = st_of(&r);
            double want_pit = KEY_BASE + (key - 1) / 10.0, cents;
            run_block(&r, NULL, out);
            cents = 100.0 * (osc_pitch(&r) - want_pit);
            if (fabs(cents) > worst) worst = fabs(cents);
            CHECK(fabs(st->pit - want_pit) < 1.0e-4, "Key %d held pitch %g, want %g", key, (double)st->pit, want_pit);
            CHECK(fabs(cents) < 0.25, "Key %d: increment off by %.3f cents", key, cents);
            CHECK(fabs((double)st->osc[0].inc * st->osc[0].rinc - 1.0) < 1.0e-4, "Key %d: inc * rinc = %g",
                  key, (double)st->osc[0].inc * st->osc[0].rinc);
            CHECK(fabs(st->osc[0].g - AMP) < 0.005 * AMP, "Key %d: gain %g", key, (double)st->osc[0].g);
            for (i = 0; i < 2 * BLK; i++)
                CHECK(isfinite(out[i]) && fabs(out[i]) <= 0.26, "Key %d: sample %g", key, (double)out[i]);
        }
    }
    printf("    worst increment error over all Key values: %.3f cents\n", worst);

    {                                       /* audible pitch, by zero crossings */
        double worst_cents = 0.0;
        int note, step = 0;
        for (note = 21; note <= 108; note += 3, step = (step + 3) % 10) {
            double pitch = note + step / 10.0;
            double hz, want = 440.0 * pow(2.0, (pitch - 69.0) / 12.0), cents;
            rig_init(&r);
            set_knob(&r, K_WAVE1, W_SINE);
            set_knob(&r, K_WAVE2, 0);
            set_knob(&r, K_KEY, KEYOF(note) + step);
            run_blocks(&r, 600);
            hz = measure_hz(&r, 8000);
            cents = 1200.0 * log2(hz / want);
            if (fabs(cents) > worst_cents) worst_cents = fabs(cents);
            CHECK(fabs(cents) < 0.5, "pitch %.1f: %.3f Hz, want %.3f Hz (%.2f cents)", pitch, hz, want, cents);
        }
        printf("    worst measured pitch error, notes 21..108 and the steps between: %.3f cents\n", worst_cents);
    }
    end();
}

/* largest step between neighbouring samples of channel A over n blocks */
static float prev_s;
static double max_delta(rig_t *r, int nblocks)
{
    float out[2 * BLK];
    double m = 0.0;
    int b, i;
    for (b = 0; b < nblocks; b++) {
        run_block(r, NULL, out);
        for (i = 0; i < BLK; i++) {
            double d = fabs((double)out[i] - (double)prev_s);
            if (d > m) m = d;
            prev_s = out[i];
        }
    }
    return m;
}

/* ---- 4. clicks -------------------------------------------------------- */
static void test_clicks(void)
{
    static const int notes[] = { 33, 57, 81 };
    static const int waves[] = { W_SINE, W_TRI };
    rig_t r;
    int n, w, j;

    begin("4. no clicks at gate on/off, legato, bypass and knob jumps");

    for (w = 0; w < 2; w++)
        for (n = 0; n < 3; n++) {
            double inc = note_inc(notes[n]);
            double slope = (waves[w] == W_SINE ? 2.0 * PI : 4.0) * inc * AMP;
            /* fastest envelope: one block moves the gain by at most
             * tab[ENV+0] of AMP, spread over 16 samples */
            double ramp = AMP * 0.3044 / BLK;
            double m = 0.0, allow = slope * 1.05 + ramp + 1.0e-4, d;
            rig_init(&r);
            set_knob(&r, K_WAVE1, waves[w]);
            set_knob(&r, K_WAVE2, 0);
            set_knob(&r, K_ATTACK, 0);
            set_knob(&r, K_RELEASE, 0);
            run_blocks(&r, 600);            /* let the level smoother settle */
            prev_s = 0.0f;
            for (j = 0; j < 60; j++) {
                set_knob(&r, K_KEY, KEYOF(notes[n]));
                d = max_delta(&r, 37 + j); if (d > m) m = d;
                set_knob(&r, K_KEY, 0);
                d = max_delta(&r, 41 + 2 * j); if (d > m) m = d;
            }
            CHECK(m <= allow, "gate %s note %d: step %.5f > %.5f",
                  w ? "tri" : "sine", notes[n], m, allow);
            printf("    gate on/off %-4s note %2d: largest step %.5f (limit %.5f, a hard gate would be up to %.2f)\n",
                   w ? "tri" : "sine", notes[n], m, allow, AMP);
        }

    {                                       /* legato: pitch changes only */
        static const int seq[] = { 57, 69, 45, 64, 52, 76, 40, 81, 57 };
        double m = 0.0, d, allow = 2.0 * PI * note_inc(81) * AMP * 1.05 + 1.0e-4;
        rig_init(&r);
        set_knob(&r, K_WAVE1, W_SINE);
        set_knob(&r, K_WAVE2, 0);
        set_knob(&r, K_KEY, KEYOF(seq[0]));
        run_blocks(&r, 600);
        prev_s = r.bus[BLK - 1];
        for (j = 0; j < 200; j++) {
            set_knob(&r, K_KEY, KEYOF(seq[j % 9]));
            d = max_delta(&r, 23 + (j % 17)); if (d > m) m = d;
        }
        CHECK(m <= allow, "legato: step %.5f > %.5f", m, allow);
        printf("    legato sine: largest step %.5f (limit %.5f)\n", m, allow);
    }

    {                                       /* bypass stepped hard, both ways */
        double inc = note_inc(57), m = 0.0, d;
        double allow = 2.0 * PI * inc * AMP * 1.05 + AMP / BLK + 1.0e-4;
        rig_init(&r);
        set_knob(&r, K_WAVE1, W_SINE);
        set_knob(&r, K_WAVE2, 0);
        set_knob(&r, K_KEY, KEYOF(57));
        run_blocks(&r, 600);
        prev_s = r.bus[BLK - 1];
        for (j = 0; j < 80; j++) {
            set_fade(&r, (j & 1) ? 1.0f : 0.0f);
            d = max_delta(&r, 19 + j); if (d > m) m = d;
        }
        CHECK(m <= allow, "bypass step: %.5f > %.5f", m, allow);
        printf("    bypass stepped 0/1: largest step %.5f (limit %.5f)\n", m, allow);
    }

    {                                       /* knob jumps while a note sounds */
        static const int jk[] = { K_LEVEL, K_OSCMIX, K_LFO, K_WAVE2, K_GLIDE };
        int q;
        for (q = 0; q < 5; q++) {
            double inc = note_inc(57), m = 0.0, d;
            /* Level up to 1.5x; smoothed knobs move 3.24 % per block */
            double allow = 2.0 * PI * inc * AMP * 1.5 * 1.05
                         + 1.5 * AMP * 0.0324 / BLK + 1.0e-4;
            rig_init(&r);
            set_knob(&r, K_WAVE1, W_SINE);
            set_knob(&r, K_WAVE2, W_SINE + 1);
            set_knob(&r, K_DETUNE, 0);
            set_knob(&r, K_LFO, LFO_TRM(20));
            set_knob(&r, K_RATE, 0);
            set_knob(&r, K_KEY, KEYOF(57));
            run_blocks(&r, 600);
            prev_s = r.bus[BLK - 1];
            for (j = 0; j < 80; j++) {
                set_knob(&r, jk[q], (j & 1) ? KS_KNOBS[jk[q]].max : 0);
                d = max_delta(&r, 29 + j); if (d > m) m = d;
            }
            CHECK(m <= allow, "%s jump: step %.5f > %.5f", K_NAME[jk[q]], m, allow);
            printf("    %-6s jumped 0/max: largest step %.5f (limit %.5f)\n", K_NAME[jk[q]], m, allow);
        }
    }
    end();
}

static double peak_of(rig_t *r, int nblocks)
{
    float out[2 * BLK];
    double m = 0.0;
    int b, i;
    for (b = 0; b < nblocks; b++) {
        run_block(r, NULL, out);
        for (i = 0; i < BLK; i++) if (fabs(out[i]) > m) m = fabs(out[i]);
    }
    return m;
}

/* ---- 5. bypass fade --------------------------------------------------- */
static void test_bypass(void)
{
    rig_t r;
    float dry[2 * BLK], out[2 * BLK];
    int b, i, k, same = 1;
    double p;

    begin("5. bypass fade: 0 is the untouched input, 1 is the full voice");

    rig_init(&r);
    set_fade(&r, 0.0f);
    for (b = 0; b < 5000; b++) {
        if ((rnd() & 7) == 0) {
            k = (int)(rnd() % K_COUNT);
            set_knob(&r, k, (int)(rnd() % (unsigned)(KS_KNOBS[k].max + 1)));
        }
        for (i = 0; i < 2 * BLK; i++) dry[i] = rnd_pm(0.9f);
        run_block(&r, dry, out);
        if (memcmp(dry, out, sizeof dry) != 0) same = 0;
    }
    CHECK(same, "fade 0: output differs from the input");

    rig_init(&r);
    set_knob(&r, K_WAVE1, W_SINE);
    set_knob(&r, K_WAVE2, 0);
    set_knob(&r, K_KEY, KEYOF(57));
    run_blocks(&r, 800);
    p = peak_of(&r, 800);
    CHECK(fabs(p - AMP) < 0.01 * AMP, "fade 1: peak %.5f, want %.5f", p, AMP);
    printf("    fade 1.0: sine peak %.5f (Level 80 -> %.2f)\n", p, AMP);

    set_fade(&r, 0.5f);
    run_blocks(&r, 10);
    p = peak_of(&r, 800);
    CHECK(fabs(p - 0.5 * AMP) < 0.01 * AMP, "fade 0.5: peak %.5f", p);
    printf("    fade 0.5: sine peak %.5f\n", p);

    set_fade(&r, 0.0f);
    run_blocks(&r, 1);                      /* the one block that ramps out */
    same = 1;
    for (b = 0; b < 500; b++) {
        for (i = 0; i < 2 * BLK; i++) dry[i] = rnd_pm(0.9f);
        run_block(&r, dry, out);
        if (memcmp(dry, out, sizeof dry) != 0) same = 0;
    }
    CHECK(same, "fade back to 0 while a note is held: output differs from the input");
    end();
}

/* ---- 6. knob decode, tables, voice behaviour -------------------------- */
static void test_details(const char *tables_bin)
{
    rig_t r;
    int k, v, i;

    begin("6. knob decode, tables, envelope, oscillator 2 pitch, detune, LFO");

    for (k = 0; k < K_COUNT; k++) {
        CHECK(strcmp(K_NAME[k], KS_KNOBS[k].name) == 0, "knob %d is '%s' in the manifest, harness expects '%s'",
              k, KS_KNOBS[k].name, K_NAME[k]);
        if (KS_KNOBS[k].vol) continue;
        for (v = 0; v <= KS_KNOBS[k].max; v++) {
            /* the kernel's KS_CLICK, mirrored */
            int got = (int)(knob_coef(k, v) * (float)KS_KNOBS[k].max + 0.5f);
            CHECK(got == v, "%s click %d decodes as %d", K_NAME[k], v, got);
            CHECK(knob_coef(k, v) <= 1.01f, "%s click %d: coefficient %g", K_NAME[k], v, (double)knob_coef(k, v));
        }
    }
    CHECK(SH_STATE_BYTES == KS_STATE_BYTES && KS_STATE_BYTES_MANIFEST == KS_STATE_BYTES,
          "state_bytes disagree");

    {                                       /* tables: file == compiled == maths */
        const float *t = (const float *)KS_TAB;
        FILE *f = fopen(tables_bin, "rb");
        CHECK(f != NULL, "cannot open %s", tables_bin);
        if (f) {
            static uint32_t buf[KS_TAB_WORDS + 1];
            size_t n = fread(buf, 4, KS_TAB_WORDS + 1, f);
            fclose(f);
            CHECK(n == KS_TAB_WORDS && memcmp(buf, KS_TAB, sizeof KS_TAB) == 0,
                  "%s differs from the tables compiled into the harness", tables_bin);
        }
        for (i = 0; i < KS_N_NOTES; i++) {
            double want = 440.0 * pow(2.0, (i - 69) / 12.0) / FS;
            CHECK(fabs(t[KS_TAB_INC + i] / want - 1.0) < 1.0e-6, "INC[%d]", i);
            CHECK(fabs((double)t[KS_TAB_INC + i] * t[KS_TAB_RINC + i] - 1.0) < 1.0e-6, "RINC[%d]", i);
        }
        for (i = 0; i < KS_N_SEMI; i++) {
            CHECK(fabs(t[KS_TAB_SEMI + i] / pow(2.0, (i - 24) / 12.0) - 1.0) < 1.0e-6, "SEMI[%d]", i);
            CHECK(fabs((double)t[KS_TAB_SEMI + i] * t[KS_TAB_SEMI + 48 - i] - 1.0) < 1.0e-6, "SEMI mirror %d", i);
        }
        for (i = 0; i < KS_N_STEPS; i++) {
            CHECK(t[KS_TAB_ENV + i] > 0.0f && t[KS_TAB_ENV + i] < 1.0f, "ENV[%d]", i);
            CHECK(t[KS_TAB_LFO + i] > 0.0f && t[KS_TAB_LFO + i] < 0.01f, "LFO[%d]", i);
            CHECK(t[KS_TAB_GLD + i] > 0.0f && t[KS_TAB_GLD + i] <= 1.0f, "GLD[%d]", i);
            if (i) {
                CHECK(t[KS_TAB_ENV + i] < t[KS_TAB_ENV + i - 1], "ENV not falling at %d", i);
                CHECK(t[KS_TAB_LFO + i] > t[KS_TAB_LFO + i - 1], "LFO not rising at %d", i);
                CHECK(t[KS_TAB_GLD + i] < t[KS_TAB_GLD + i - 1], "GLD not falling at %d", i);
            }
        }
        CHECK(t[KS_TAB_GLD] == 1.0f, "GLD[0] must be exactly 1 (Glide 0 lands at once)");
    }

    {                                       /* attack time constant */
        double tau = 0.001 * pow(3000.0, 0.5), t63 = -1.0;
        int b;
        rig_init(&r);
        set_knob(&r, K_ATTACK, 50);
        set_knob(&r, K_KEY, KEYOF(57));
        for (b = 1; b <= 20000 && t63 < 0.0; b++) {
            run_blocks(&r, 1);
            if (st_of(&r)->env >= 0.6321f) t63 = b * BLK / FS;
        }
        CHECK(fabs(t63 - tau) < 0.03 * tau + BLK / FS, "Atk 50: 63 %% after %.4f s, want %.4f s", t63, tau);
        printf("    Atk 50: 63 %% after %.1f ms (table: %.1f ms)\n", t63 * 1e3, tau * 1e3);
    }

    {                                       /* release: note held, then exact idle */
        float dry[2 * BLK], out[2 * BLK];
        int b, held = 1, idle_at = -1;
        rig_init(&r);
        set_knob(&r, K_RELEASE, 40);
        set_knob(&r, K_KEY, KEYOF(60));
        run_blocks(&r, 600);
        set_knob(&r, K_KEY, 0);
        for (b = 0; b < 40000 && idle_at < 0; b++) {
            run_blocks(&r, 1);
            if (fabsf(st_of(&r)->pit - 60.0f) > 1.0e-4f) held = 0;
            if (st_of(&r)->env == 0.0f && st_of(&r)->osc[0].g == 0.0f && st_of(&r)->osc[1].g == 0.0f)
                idle_at = b;
        }
        CHECK(held, "release did not hold the pitch");
        CHECK(idle_at > 0, "release never reached idle");
        for (i = 0; i < 2 * BLK; i++) dry[i] = rnd_pm(0.9f);
        run_block(&r, dry, out);
        CHECK(memcmp(dry, out, sizeof dry) == 0, "idle voice touched the bus");
        printf("    Rel 40: idle after %.0f ms, bus untouched afterwards\n", idle_at * BLK / FS * 1e3);
    }

    {                                       /* interval and detune of oscillator 2 */
        static const int iv[] = { 0, 12, 24, 31, 36, 48 };
        double base, hz;
        int q;
        rig_init(&r);
        set_knob(&r, K_WAVE1, W_SINE);
        set_knob(&r, K_WAVE2, W_SINE + 1);
        set_knob(&r, K_OSCMIX, 100);        /* oscillator 2 alone */
        set_knob(&r, K_DETUNE, 0);
        set_knob(&r, K_KEY, KEYOF(57));
        base = 440.0 * pow(2.0, (57 - 69) / 12.0);
        for (q = 0; q < 6; q++) {
            double want = base * pow(2.0, (iv[q] - 24) / 12.0), cents;
            set_knob(&r, K_PITCH, iv[q]);
            run_blocks(&r, 800);
            hz = measure_hz(&r, 8000);
            cents = 1200.0 * log2(hz / want);
            CHECK(fabs(cents) < 1.0, "Pitch %+d: %.3f Hz, want %.3f Hz", iv[q] - 24, hz, want);
        }
        set_knob(&r, K_PITCH, 24);
        set_knob(&r, K_DETUNE, 100);
        run_blocks(&r, 800);
        hz = measure_hz(&r, 8000);
        CHECK(fabs(1200.0 * log2(hz / base) - 50.0) < 1.0, "Dtune 100: %.2f cents", 1200.0 * log2(hz / base));
        printf("    Dtune 100: +%.2f cents\n", 1200.0 * log2(hz / base));
    }

    {                                       /* vibrato range, tremolo range */
        double lo = 1e9, hi = -1e9, glo = 1e9, ghi = -1e9, base = note_inc(57);
        int b;
        rig_init(&r);
        set_knob(&r, K_WAVE2, 0);
        set_knob(&r, K_LFO, LFO_VIB(50));
        set_knob(&r, K_RATE, 80);
        set_knob(&r, K_KEY, KEYOF(57));
        run_blocks(&r, 800);
        for (b = 0; b < 6000; b++) {
            double c;
            run_blocks(&r, 1);
            c = 1200.0 * log2(st_of(&r)->osc[0].inc / base);
            if (c < lo) lo = c;
            if (c > hi) hi = c;
        }
        CHECK(hi > 99.0 && hi < 100.5 && lo < -99.0 && lo > -100.5, "vibrato %.2f..%.2f cents", lo, hi);
        printf("    LFO Vib50: %.2f .. +%.2f cents\n", lo, hi);

        set_knob(&r, K_LFO, LFO_TRM(50));
        run_blocks(&r, 800);
        for (b = 0; b < 6000; b++) {
            double g;
            run_blocks(&r, 1);
            g = st_of(&r)->osc[0].g;
            if (g < glo) glo = g;
            if (g > ghi) ghi = g;
        }
        CHECK(glo < 0.01 * AMP && ghi > 0.99 * AMP && ghi <= AMP * 1.001, "tremolo gain %.4f..%.4f", glo, ghi);
        printf("    LFO Trm50: gain %.4f .. %.4f\n", glo, ghi);
    }
    end();
}

/* amplitude of the component at hz: Hann-windowed single-bin DFT */
static double tone_amp(const float *x, long n, double hz)
{
    double re = 0.0, im = 0.0, wsum = 0.0;
    long i;
    for (i = 0; i < n; i++) {
        double w = 0.5 - 0.5 * cos(2.0 * PI * (double)i / (double)(n - 1));
        double a = 2.0 * PI * hz * (double)i / FS;
        re += w * x[i] * cos(a);
        im += w * x[i] * sin(a);
        wsum += w;
    }
    return 2.0 * sqrt(re * re + im * im) / wsum;
}

#define SPEC_N 65536
static float spec_x[SPEC_N];

static void capture(rig_t *r, int midi, int wave)
{
    float out[2 * BLK];
    long n = 0;
    rig_init(r);
    set_knob(r, K_WAVE1, wave);
    set_knob(r, K_WAVE2, 0);
    set_knob(r, K_KEY, KEYOF(midi));
    run_blocks(r, 800);
    while (n < SPEC_N) {
        run_block(r, NULL, out);
        memcpy(spec_x + n, out, BLK * sizeof(float));
        n += BLK;
    }
}

/* ---- 7. oscillator spectra -------------------------------------------- */
static void test_spectra(void)
{
    const float *t = (const float *)KS_TAB;
    rig_t r;
    double f = (double)t[KS_TAB_INC + 45] * FS;         /* 110 Hz */
    double a1, worst;
    int n;

    begin("7. oscillator spectra: harmonics as designed, aliasing held down");

    capture(&r, 45, W_SAW);
    worst = 0.0;
    for (n = 1; n <= 10; n++) {
        double got = tone_amp(spec_x, SPEC_N, n * f), want = 2.0 * AMP / (PI * n);
        double e = fabs(got / want - 1.0);
        if (e > worst) worst = e;
        CHECK(e < 0.01, "saw harmonic %d: %.5f, want %.5f", n, got, want);
    }
    printf("    saw 110 Hz: harmonics 1..10 within %.2f %% of 1/n\n", worst * 100.0);

    capture(&r, 45, W_SQR);
    a1 = tone_amp(spec_x, SPEC_N, f);
    worst = 0.0;
    for (n = 1; n <= 9; n++) {
        double got = tone_amp(spec_x, SPEC_N, n * f);
        if (n & 1) {
            double want = 4.0 * AMP / (PI * n), e = fabs(got / want - 1.0);
            if (e > worst) worst = e;
            CHECK(e < 0.01, "square harmonic %d: %.5f, want %.5f", n, got, want);
        } else {
            CHECK(got < 0.005 * a1, "square even harmonic %d: %.5f", n, got);
        }
    }
    printf("    square 110 Hz: odd harmonics within %.2f %% of 1/n, even ones absent\n", worst * 100.0);

    capture(&r, 45, W_TRI);
    a1 = tone_amp(spec_x, SPEC_N, f);
    worst = 0.0;
    for (n = 1; n <= 7; n++) {
        double got = tone_amp(spec_x, SPEC_N, n * f);
        if (n & 1) {
            double want = 8.0 * AMP / (PI * PI * n * n), e = fabs(got / want - 1.0);
            if (e > worst) worst = e;
            CHECK(e < 0.01, "triangle harmonic %d: %.5f, want %.5f", n, got, want);
        } else {
            CHECK(got < 0.005 * a1, "triangle even harmonic %d: %.5f", n, got);
        }
    }
    printf("    triangle 110 Hz: odd harmonics within %.2f %% of 1/n^2\n", worst * 100.0);

    capture(&r, 45, W_SINE);
    a1 = tone_amp(spec_x, SPEC_N, f);
    worst = 0.0;
    CHECK(fabs(a1 / AMP - 1.0) < 0.005, "sine fundamental %.5f", a1);
    for (n = 2; n <= 9; n++) {
        double got = tone_amp(spec_x, SPEC_N, n * f) / a1;
        if (got > worst) worst = got;
        CHECK(got < 0.005, "sine harmonic %d at %.2f %%", n, got * 100.0);
    }
    printf("    sine 110 Hz: strongest overtone %.3f %% (%.0f dB)\n", worst * 100.0, 20.0 * log10(worst));

    {   /* aliasing: note 96, harmonic 21 folds down next to 147 Hz */
        double fh = (double)t[KS_TAB_INC + 96] * FS, alias = fabs(21.0 * fh - FS);
        double naive = 2.0 * AMP / (PI * 21.0), got, fund;
        capture(&r, 96, W_SAW);
        fund = tone_amp(spec_x, SPEC_N, fh);
        got = tone_amp(spec_x, SPEC_N, alias);
        CHECK(got < naive / 5.0, "saw alias at %.1f Hz: %.6f (a naive saw has %.6f)", alias, got, naive);
        printf("    saw %.0f Hz: alias at %.0f Hz is %.0f dB below the fundamental (naive saw: %.0f dB)\n",
               fh, alias, -20.0 * log10(got / fund), -20.0 * log10(naive / fund));
        /* the less flattering case: harmonic 15 folds once, into the audible band */
        alias = fabs(15.0 * fh - FS);
        naive = 2.0 * AMP / (PI * 15.0);
        got = tone_amp(spec_x, SPEC_N, alias);
        CHECK(got < naive, "saw alias at %.1f Hz: %.6f (a naive saw has %.6f)", alias, got, naive);
        printf("    saw %.0f Hz: alias at %.0f Hz is %.0f dB below the fundamental (naive saw: %.0f dB)\n",
               fh, alias, -20.0 * log10(got / fund), -20.0 * log10(naive / fund));
    }
    end();
}

/* blocks until the pitch has covered 63.21 % of the way from a to b */
static double time_to_63(rig_t *r, double a, double b, int max_blocks)
{
    int n;
    for (n = 1; n <= max_blocks; n++) {
        run_blocks(r, 1);
        if ((st_of(r)->pit - a) / (b - a) >= 0.6321) return n * BLK / FS;
    }
    return -1.0;
}

/* ---- 8. glide and pitch bend ------------------------------------------ */
static void test_glide_bend(void)
{
    rig_t r;
    int j, k;

    begin("8. glide between tied notes, bend smoothing, a note after a gap");

    {                                       /* Glide 0: a tied note lands in one block */
        rig_init(&r);
        set_knob(&r, K_KEY, KEYOF(45));
        run_blocks(&r, 300);
        set_knob(&r, K_KEY, KEYOF(57));
        run_blocks(&r, 1);
        CHECK(fabs(st_of(&r)->pit - 57.0) < 1.0e-3, "Glide 0: pitch %g one block after a tied note", (double)st_of(&r)->pit);
        run_blocks(&r, 1);
        CHECK(!(st_of(&r)->flags & KS_FLAG_GLIDE), "Glide 0: slide flag still set");
    }

    {                                       /* the Glide knob is a time constant */
        static const int gk[] = { 1, 25, 50, 75, 100 };
        for (k = 0; k < 5; k++) {
            double tau = 0.020 * pow(0.7 / 0.020, (gk[k] - 1) / 99.0), t63;
            rig_init(&r);
            set_knob(&r, K_GLIDE, gk[k]);
            set_knob(&r, K_KEY, KEYOF(45));
            run_blocks(&r, 300);
            CHECK(fabs(st_of(&r)->pit - 45.0) < 1.0e-3, "Glide %d: first note not on pitch (%g)", gk[k], (double)st_of(&r)->pit);
            set_knob(&r, K_KEY, KEYOF(57));
            t63 = time_to_63(&r, 45.0, 57.0, 200000);
            CHECK(fabs(t63 - tau) < 0.03 * tau + BLK / FS, "Glide %d: 63 %% after %.4f s, want %.4f s", gk[k], t63, tau);
            printf("    Glide %3d: 63 %% of an octave after %6.1f ms (table: %6.1f ms)\n", gk[k], t63 * 1e3, tau * 1e3);
            run_blocks(&r, (int)(12.0 * tau * FS / BLK));
            CHECK(fabs(st_of(&r)->pit - 57.0) < 2.0e-3, "Glide %d: never arrived (%g)", gk[k], (double)st_of(&r)->pit);
            CHECK(!(st_of(&r)->flags & KS_FLAG_GLIDE), "Glide %d: slide flag never cleared", gk[k]);
        }
    }

    {                                       /* a note after a gap starts on pitch */
        rig_init(&r);
        set_knob(&r, K_GLIDE, 100);
        set_knob(&r, K_RELEASE, 80);        /* the old note is still ringing */
        set_knob(&r, K_KEY, KEYOF(45));
        run_blocks(&r, 300);
        set_knob(&r, K_KEY, 0);
        run_blocks(&r, 1);
        set_knob(&r, K_KEY, KEYOF(69));
        run_blocks(&r, 1);
        CHECK(fabs(st_of(&r)->pit - 69.0) < 1.0e-3, "after a one-block gap the note slid (pitch %g)", (double)st_of(&r)->pit);
        CHECK(st_of(&r)->env > 0.0f, "the release was cut off");
    }

    for (k = 0; k < 2; k++) {               /* bend step: smooth, quick, not slowed by Glide */
        double before, after, maxmove = 0.0, prev;
        rig_init(&r);
        set_knob(&r, K_GLIDE, k ? 100 : 0);
        set_knob(&r, K_KEY, KEYOF(45));
        run_blocks(&r, 600);
        before = st_of(&r)->pit;
        set_knob(&r, K_KEY, KEYOF(45) + 1);                 /* +10 cents */
        prev = before;
        for (j = 0; j < 276; j++) {                         /* 100 ms */
            run_blocks(&r, 1);
            if (fabs(st_of(&r)->pit - prev) > maxmove) maxmove = fabs(st_of(&r)->pit - prev);
            CHECK(st_of(&r)->pit >= prev - 1.0e-6, "bend step went backwards");
            prev = st_of(&r)->pit;
        }
        after = st_of(&r)->pit;
        CHECK(maxmove < 0.1 * 0.0185, "Glide %d: a 10-cent step moved %.4f cents in one block", k ? 100 : 0, maxmove * 100.0);
        CHECK(fabs(after - before - 0.1) < 0.002, "Glide %d: 10-cent step is at %.3f cents after 100 ms", k ? 100 : 0, (after - before) * 100.0);
        CHECK(!(st_of(&r)->flags & KS_FLAG_GLIDE), "a bend step started a slide");
        printf("    10-cent bend step, Glide %3d: largest move per block %.3f cents, %.2f cents after 100 ms\n",
               k ? 100 : 0, maxmove * 100.0, (after - before) * 100.0);
    }

    {                                       /* 9 clicks are a bend, 10 are a note */
        rig_init(&r);
        set_knob(&r, K_GLIDE, 60);
        set_knob(&r, K_KEY, KEYOF(45));
        run_blocks(&r, 600);
        set_knob(&r, K_KEY, KEYOF(45) + 9);
        run_blocks(&r, 1);
        CHECK(!(st_of(&r)->flags & KS_FLAG_GLIDE), "a 90-cent move was taken for a note");
        run_blocks(&r, 600);
        CHECK(fabs(st_of(&r)->pit - 45.9) < 1.0e-3, "90-cent bend ended at %g", (double)st_of(&r)->pit);
        set_knob(&r, K_KEY, KEYOF(45) + 19);
        run_blocks(&r, 1);
        CHECK(st_of(&r)->flags & KS_FLAG_GLIDE, "a 100-cent move was taken for a bend");
    }

    {                                       /* the wheel moves while a slide runs */
        double prev;
        int mono = 1;
        rig_init(&r);
        set_knob(&r, K_GLIDE, 60);
        set_knob(&r, K_KEY, KEYOF(45));
        run_blocks(&r, 600);
        set_knob(&r, K_KEY, KEYOF(57));
        run_blocks(&r, 20);
        prev = st_of(&r)->pit;
        for (j = 0; j < 5; j++) {           /* target creeps up 50 cents */
            set_knob(&r, K_KEY, KEYOF(57) + j + 1);
            run_blocks(&r, 28);
            CHECK(st_of(&r)->flags & KS_FLAG_GLIDE, "a bend during the slide ended the slide");
            if (st_of(&r)->pit < prev) mono = 0;
            prev = st_of(&r)->pit;
        }
        CHECK(mono, "the slide went backwards while the target moved up");
        run_blocks(&r, 20000);
        CHECK(fabs(st_of(&r)->pit - 57.5) < 2.0e-3, "slide with a moving target ended at %g", (double)st_of(&r)->pit);
    }

    {                                       /* no clicks while sliding and bending */
        static const int seq[] = { 45, 57, 40, 64, 33, 69, 52, 76, 45 };
        double m = 0.0, d, allow = 2.0 * PI * note_inc(76.5) * AMP * 1.05 + 1.0e-4;
        rig_init(&r);
        set_knob(&r, K_WAVE1, W_SINE);
        set_knob(&r, K_WAVE2, 0);
        set_knob(&r, K_GLIDE, 30);
        set_knob(&r, K_KEY, KEYOF(seq[0]));
        run_blocks(&r, 600);
        prev_s = r.bus[BLK - 1];
        for (j = 0; j < 400; j++) {
            int bend = (int)(rnd() % 9) - 4;
            set_knob(&r, K_KEY, KEYOF(seq[(j / 4) % 9]) + bend);
            if ((j & 31) == 31) set_knob(&r, K_GLIDE, (int)(rnd() % 101));
            d = max_delta(&r, 7 + (j % 23)); if (d > m) m = d;
        }
        CHECK(m <= allow, "slides and bends: step %.5f > %.5f", m, allow);
        printf("    slides and bends on a sine: largest step %.5f (limit %.5f)\n", m, allow);
    }
    end();
}

/* -------------------------------------------------------------- render */

typedef struct { int16_t *d; long n, cap; } wav_t;

static void wav_play(rig_t *r, wav_t *w, double seconds)
{
    float out[2 * BLK];
    long nb = (long)(seconds * FS / BLK + 0.5);
    int i;
    while (nb-- > 0) {
        run_block(r, NULL, out);
        if (w->n + BLK > w->cap) {
            w->cap = w->cap ? w->cap * 2 : 1 << 16;
            w->d = realloc(w->d, (size_t)w->cap * sizeof *w->d);
            if (!w->d) { perror("realloc"); exit(2); }
        }
        for (i = 0; i < BLK; i++) {
            double v = out[i] * 32767.0 * 2.0;      /* +6 dB so it is not too quiet */
            if (v > 32767.0) v = 32767.0;
            if (v < -32767.0) v = -32767.0;
            w->d[w->n++] = (int16_t)lrint(v);
        }
    }
}

static void put32(FILE *f, uint32_t v) { fputc(v & 255, f); fputc((v >> 8) & 255, f); fputc((v >> 16) & 255, f); fputc(v >> 24, f); }
static void put16(FILE *f, unsigned v) { fputc(v & 255, f); fputc((v >> 8) & 255, f); }

static void wav_save(wav_t *w, const char *dir, const char *name)
{
    char path[1024];
    FILE *f;
    long i;
    snprintf(path, sizeof path, "%s/%s", dir, name);
    f = fopen(path, "wb");
    if (!f) { perror(path); exit(2); }
    fwrite("RIFF", 1, 4, f); put32(f, (uint32_t)(36 + w->n * 2)); fwrite("WAVEfmt ", 1, 8, f);
    put32(f, 16); put16(f, 1); put16(f, 1); put32(f, 44100); put32(f, 88200); put16(f, 2); put16(f, 16);
    fwrite("data", 1, 4, f); put32(f, (uint32_t)(w->n * 2));
    for (i = 0; i < w->n; i++) put16(f, (uint16_t)w->d[i]);
    fclose(f);
    printf("  %-28s %5.1f s\n", name, w->n / FS);
    free(w->d);
    memset(w, 0, sizeof *w);
}

static void note(rig_t *r, wav_t *w, int midi, double on, double off)
{
    set_knob(r, K_KEY, KEYOF(midi));
    wav_play(r, w, on);
    if (off > 0.0) {
        set_knob(r, K_KEY, 0);
        wav_play(r, w, off);
    }
}

static void phrase(rig_t *r, wav_t *w)
{
    static const int p[] = { 33, 45, 40, 43, 45, 52, 45, 33 };
    int i;
    for (i = 0; i < 8; i++) note(r, w, p[i], 0.26, 0.06);
    wav_play(r, w, 0.5);
}

/* Pitch bend the way the bridge sends it: the wheel position becomes whole
 * Key clicks (10 cents), one message about every 10 ms, and no message moves
 * the pitch by more than 9 clicks, so the kernel never takes it for a note. */
#define BEND_MSG_BLOCKS 28                  /* 28 blocks = 10.2 ms */
#define BEND_MAX_CLICKS 9
static double bend_cur;                     /* semitones, where the wheel is */
static int    bend_note, bend_sent;

static void bend_start(rig_t *r, wav_t *w, int midi)
{
    (void)w;
    bend_note = midi;
    bend_sent = (int)lrint(bend_cur * 10.0);
    set_knob(r, K_KEY, KEYOF(midi) + bend_sent);
}

static void bend_msg(rig_t *r, wav_t *w)
{
    int want = (int)lrint(bend_cur * 10.0);
    if (want > bend_sent + BEND_MAX_CLICKS) want = bend_sent + BEND_MAX_CLICKS;
    if (want < bend_sent - BEND_MAX_CLICKS) want = bend_sent - BEND_MAX_CLICKS;
    bend_sent = want;
    set_knob(r, K_KEY, KEYOF(bend_note) + bend_sent);
    wav_play(r, w, BEND_MSG_BLOCKS * BLK / FS);
}

static void bend_hold(rig_t *r, wav_t *w, double seconds)
{
    int n = (int)(seconds * FS / (BEND_MSG_BLOCKS * BLK) + 0.5);
    while (n-- > 0) bend_msg(r, w);
}

static void bend_to(rig_t *r, wav_t *w, double semis, double seconds)
{
    int n = (int)(seconds * FS / (BEND_MSG_BLOCKS * BLK) + 0.5), i;
    double from = bend_cur;
    if (n < 1) n = 1;
    for (i = 1; i <= n; i++) {
        bend_cur = from + (semis - from) * i / n;
        bend_msg(r, w);
    }
}

static void render(const char *dir)
{
    rig_t r;
    wav_t w = { 0 };
    int i, j;

    printf("rendering into %s (mono, 16 bit, +6 dB)\n", dir);

    {   /* scale, default patch */
        static const int sc[] = { 36, 38, 40, 41, 43, 45, 47, 48, 50, 52, 53, 55, 57, 59, 60 };
        rig_init(&r);
        wav_play(&r, &w, 0.3);
        for (i = 0; i < 15; i++) note(&r, &w, sc[i], 0.28, 0.07);
        wav_play(&r, &w, 1.0);
        wav_save(&w, dir, "01_scale_default_sound.wav");
    }
    {   /* legato, square, one oscillator */
        static const int lg[] = { 36, 38, 40, 43, 45, 48, 45, 43, 40, 38, 36, 31, 33, 36 };
        rig_init(&r);
        set_knob(&r, K_WAVE1, W_SQR);
        set_knob(&r, K_WAVE2, 0);
        wav_play(&r, &w, 0.3);
        for (i = 0; i < 14; i++) note(&r, &w, lg[i], 0.19, 0.0);
        set_knob(&r, K_KEY, 0);
        wav_play(&r, &w, 1.0);
        wav_save(&w, dir, "02_legato_square.wav");
    }
    {   /* sixteenths at 140 BPM */
        static const int rf[] = { 28, 28, 40, 28, 31, 28, 40, 35, 28, 28, 40, 28, 33, 35, 38, 40 };
        double s16 = 60.0 / 140.0 / 4.0;
        rig_init(&r);
        wav_play(&r, &w, 0.3);
        for (j = 0; j < 4; j++)
            for (i = 0; i < 16; i++) note(&r, &w, rf[i], s16 * 0.7, s16 * 0.3);
        wav_play(&r, &w, 1.0);
        wav_save(&w, dir, "03_sixteenths_140bpm.wav");
    }
    {   /* the four waveforms, one oscillator */
        rig_init(&r);
        set_knob(&r, K_WAVE2, 0);
        wav_play(&r, &w, 0.3);
        for (i = 0; i < 4; i++) { set_knob(&r, K_WAVE1, i); phrase(&r, &w); }
        wav_save(&w, dir, "04_waveforms_saw_sqr_tri_sine.wav");
    }
    {   /* two oscillators */
        rig_init(&r);
        wav_play(&r, &w, 0.3);
        set_knob(&r, K_DETUNE, 12); phrase(&r, &w);                          /* unison, light detune */
        set_knob(&r, K_DETUNE, 45); phrase(&r, &w);                          /* heavy detune */
        set_knob(&r, K_DETUNE, 6); set_knob(&r, K_PITCH, 36); phrase(&r, &w);/* +12 */
        set_knob(&r, K_PITCH, 31); phrase(&r, &w);                           /* +7 */
        set_knob(&r, K_PITCH, 12); set_knob(&r, K_WAVE2, W_SQR + 1); phrase(&r, &w); /* -12 square */
        wav_save(&w, dir, "05_two_oscillators.wav");
    }
    {   /* LFO */
        rig_init(&r);
        wav_play(&r, &w, 0.3);
        note(&r, &w, 45, 2.0, 0.5);
        set_knob(&r, K_LFO, LFO_TRM(35)); set_knob(&r, K_RATE, 60);
        note(&r, &w, 45, 2.5, 0.5);
        set_knob(&r, K_LFO, LFO_VIB(20));
        note(&r, &w, 45, 2.5, 0.5);
        set_knob(&r, K_RATE, 75); set_knob(&r, K_LFO, LFO_VIB(50));
        note(&r, &w, 45, 2.5, 1.0);
        wav_save(&w, dir, "06_lfo_off_trem_vib.wav");
    }
    {   /* envelope */
        static const int ar[][2] = { { 0, 0 }, { 5, 30 }, { 40, 50 }, { 65, 70 } };
        rig_init(&r);
        wav_play(&r, &w, 0.3);
        for (i = 0; i < 4; i++) {
            set_knob(&r, K_ATTACK, ar[i][0]);
            set_knob(&r, K_RELEASE, ar[i][1]);
            note(&r, &w, 40, 0.8, 0.9);
            note(&r, &w, 47, 0.8, 1.6);
        }
        wav_save(&w, dir, "07_envelope.wav");
    }
    {   /* glide: the same tied line with Glide 0, 25, 50, 75, then played detached */
        static const int lg[] = { 33, 45, 40, 52, 45, 57, 52, 40, 33 };
        static const int gl[] = { 0, 25, 50, 75 };
        rig_init(&r);
        wav_play(&r, &w, 0.3);
        for (j = 0; j < 4; j++) {
            set_knob(&r, K_GLIDE, gl[j]);
            for (i = 0; i < 9; i++) note(&r, &w, lg[i], 0.34, 0.0);
            set_knob(&r, K_KEY, 0);
            wav_play(&r, &w, 0.8);
        }
        set_knob(&r, K_GLIDE, 50);          /* detached: no slide although Glide is 50 */
        for (i = 0; i < 9; i++) note(&r, &w, lg[i], 0.26, 0.08);
        wav_play(&r, &w, 1.0);
        wav_save(&w, dir, "08_glide_0_25_50_75_then_detached.wav");
    }
    {   /* pitch bend as the bridge would send it */
        rig_init(&r);
        set_knob(&r, K_WAVE2, 0);           /* one saw: steps are easiest to hear */
        wav_play(&r, &w, 0.3);
        bend_start(&r, &w, 45);
        bend_hold(&r, &w, 0.6);
        bend_to(&r, &w, 2.0, 2.0);  bend_hold(&r, &w, 0.5);     /* up a whole tone in 2 s */
        bend_to(&r, &w, 0.0, 2.0);  bend_hold(&r, &w, 0.6);
        bend_to(&r, &w, 0.5, 3.0);  bend_hold(&r, &w, 0.3);     /* very slow: 50 cents in 3 s */
        bend_to(&r, &w, 0.0, 3.0);  bend_hold(&r, &w, 0.6);
        for (i = 0; i < 10; i++) {                              /* wheel vibrato, +/-30 cents */
            bend_to(&r, &w, 0.3, 0.05); bend_to(&r, &w, -0.3, 0.1); bend_to(&r, &w, 0.0, 0.05);
        }
        bend_hold(&r, &w, 0.6);
        for (i = 0; i < 4; i++) {                               /* fast flicks */
            bend_to(&r, &w, 2.0, 0.06); bend_hold(&r, &w, 0.15);
            bend_to(&r, &w, 0.0, 0.06); bend_hold(&r, &w, 0.25);
        }
        bend_to(&r, &w, -2.0, 0.8); bend_to(&r, &w, 0.0, 0.8);  /* down and back */
        set_knob(&r, K_KEY, 0);
        wav_play(&r, &w, 1.0);
        wav_save(&w, dir, "09_pitch_bend_one_saw.wav");
    }
    {   /* the same slow bends with the default two-saw patch, and a line with bends */
        static const int ln[] = { 33, 45, 40, 43 };
        rig_init(&r);
        wav_play(&r, &w, 0.3);
        bend_start(&r, &w, 33);
        bend_hold(&r, &w, 0.5);
        bend_to(&r, &w, 2.0, 2.0);  bend_hold(&r, &w, 0.4);
        bend_to(&r, &w, 0.0, 2.0);  bend_hold(&r, &w, 0.4);
        bend_to(&r, &w, 0.5, 3.0);  bend_to(&r, &w, 0.0, 3.0);
        set_knob(&r, K_KEY, 0);
        wav_play(&r, &w, 0.6);
        for (j = 0; j < 2; j++)
            for (i = 0; i < 4; i++) {       /* each note is bent into from two semitones below */
                bend_cur = -2.0;
                bend_start(&r, &w, ln[i]);
                bend_to(&r, &w, 0.0, 0.12);
                bend_hold(&r, &w, 0.25);
                set_knob(&r, K_KEY, 0);
                wav_play(&r, &w, 0.08);
                bend_cur = 0.0;
            }
        wav_play(&r, &w, 1.0);
        wav_save(&w, dir, "10_pitch_bend_default_sound.wav");
    }
}

int main(int argc, char **argv)
{
    if (argc >= 3 && strcmp(argv[1], "render") == 0) {
        render(argv[2]);
        return 0;
    }
    if (argc >= 2 && strcmp(argv[1], "test") == 0) {
        const char *bin = argc >= 3 ? argv[2] : "effects/keysynth/ks_tables.bin";
        test_uninitialised();
        test_fuzz();
        test_all_keys();
        test_clicks();
        test_bypass();
        test_details(bin);
        test_spectra();
        test_glide_bend();
        printf("\n%d checks, %d failed: %s\n", n_check, n_fail, n_fail ? "FAIL" : "PASS");
        return n_fail ? 1 : 0;
    }
    fprintf(stderr, "usage: harness test [ks_tables.bin] | harness render <dir>\n");
    return 2;
}

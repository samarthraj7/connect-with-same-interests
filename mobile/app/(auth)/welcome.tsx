import { LinearGradient } from "expo-linear-gradient";
import React, { useEffect, useRef, useState } from "react";
import {
  Animated,
  Easing,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  useWindowDimensions,
  View,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { AuthModal } from "../../components/AuthModal";
import { SignupSheet } from "../../components/SignupSheet";
import { Button } from "../../components/ui";
import { api } from "../../lib/api";
import { fonts, space } from "../../lib/theme";

const TAGLINES = [
  "Know what you share before you say hello.",
  "Walk into the room already connected.",
  "Real overlap — not generic small talk.",
  "Research once. Open with something true.",
];

const PREVIEW_LINES = [
  { kind: "name", text: "Samarth Rajendra" },
  { kind: "meta", text: "USC · Los Angeles" },
  { kind: "section", text: "Things to talk about" },
  { kind: "hook", text: "MS CS (AI) at USC, 4.0 GPA — targeting HRT, NVIDIA, Amazon Robotics, SpaceX." },
  { kind: "hook", text: "Building NavLang and FailureNet — language-conditioned nav and multimodal fault detection for robotics." },
  { kind: "section", text: "Opener" },
  { kind: "opener", text: "“I'm building a conversation-prep tool for exactly this — mind if I ask what you're working on?”" },
];

const STEPS = [
  {
    title: "Find",
    body: "Search a name. Lock the exact LinkedIn identity — not a same-name stranger.",
  },
  {
    title: "Research",
    body: "Public footprint, career, and socials synthesized into a cited briefing.",
  },
  {
    title: "Talk",
    body: "Conversation ideas from real overlap between you and them — before you meet.",
  },
];

const FALLBACK_REVIEWS = [
  {
    id: "placeholder-1",
    quote: "Walked into the meeting already knowing what we shared. Felt natural, not stalky.",
    name: "Alex M.",
    role: "Founder",
  },
  {
    id: "placeholder-2",
    quote: "The LinkedIn lock alone saved me from briefing the wrong person twice.",
    name: "Priya S.",
    role: "BD lead",
  },
  {
    id: "placeholder-3",
    quote: "Common ground tips that actually matched — not generic icebreakers.",
    name: "Jordan L.",
    role: "Investor",
  },
];

const FALLBACK_ICP = {
  headline: "Built for people who meet for a living",
  body: "Founders, operators, investors, and sales leaders who prep before intros — and refuse to mix up same-name strangers.",
  segments: ["Founders & operators", "Investors & advisors", "BD / partnerships", "Recruiters & talent"],
};
function BriefingPreview({ wide }: { wide: boolean }) {
  const opacities = useRef(PREVIEW_LINES.map(() => new Animated.Value(0))).current;
  const slides = useRef(PREVIEW_LINES.map(() => new Animated.Value(14))).current;
  const highlight = useRef(new Animated.Value(0)).current;

  useEffect(() => {
    const entrance = Animated.stagger(
      110,
      PREVIEW_LINES.map((_, i) =>
        Animated.parallel([
          Animated.timing(opacities[i], {
            toValue: 1,
            duration: 480,
            easing: Easing.out(Easing.cubic),
            useNativeDriver: true,
          }),
          Animated.timing(slides[i], {
            toValue: 0,
            duration: 480,
            easing: Easing.out(Easing.cubic),
            useNativeDriver: true,
          }),
        ]),
      ),
    );
    entrance.start();

    const loop = Animated.loop(
      Animated.sequence([
        Animated.timing(highlight, {
          toValue: 1,
          duration: 2800,
          easing: Easing.inOut(Easing.sin),
          useNativeDriver: true,
        }),
        Animated.timing(highlight, {
          toValue: 0,
          duration: 2800,
          easing: Easing.inOut(Easing.sin),
          useNativeDriver: true,
        }),
      ]),
    );
    const t = setTimeout(() => loop.start(), 900);
    return () => {
      clearTimeout(t);
      loop.stop();
    };
  }, [opacities, slides, highlight]);

  const glow = highlight.interpolate({
    inputRange: [0, 1],
    outputRange: [0.04, 0.14],
  });

  return (
    <View style={[styles.previewShell, wide && styles.previewShellWide]}>
      <Animated.View style={[styles.previewGlow, { opacity: glow }]} />
      <View style={styles.previewPaper}>
        <Text style={styles.previewEyebrow}>LIVE BRIEFING PREVIEW</Text>
        {PREVIEW_LINES.map((line, i) => (
          <Animated.View
            key={`${line.kind}-${i}`}
            style={{
              opacity: opacities[i],
              transform: [{ translateY: slides[i] }],
              marginTop: line.kind === "section" ? 16 : line.kind === "name" ? 10 : 6,
            }}
          >
            <Text
              style={
                line.kind === "name"
                  ? styles.previewName
                  : line.kind === "meta"
                    ? styles.previewMeta
                    : line.kind === "section"
                      ? styles.previewSection
                      : line.kind === "opener"
                        ? styles.previewOpener
                        : styles.previewHook
              }
            >
              {line.text}
            </Text>
          </Animated.View>
        ))}
      </View>
    </View>
  );
}

export default function Welcome() {
  const { width } = useWindowDimensions();
  const wide = width >= 820;
  const fade = useRef(new Animated.Value(0)).current;
  const rise = useRef(new Animated.Value(24)).current;
  const tagFade = useRef(new Animated.Value(1)).current;
  const tagSlide = useRef(new Animated.Value(0)).current;
  const drift = useRef(new Animated.Value(0)).current;
  const [tagIndex, setTagIndex] = useState(0);
  const [loginOpen, setLoginOpen] = useState(false);
  const [signupOpen, setSignupOpen] = useState(false);
  const [userCountDisplay, setUserCountDisplay] = useState<string | null>(null);
  const [reviews, setReviews] = useState(FALLBACK_REVIEWS);
  const [icp, setIcp] = useState(FALLBACK_ICP);

  useEffect(() => {
    let cancelled = false;
    api
      .publicStats()
      .then((s) => {
        if (cancelled) return;
        if (s.user_count_display != null) setUserCountDisplay(s.user_count_display);
        if (s.reviews?.length) setReviews(s.reviews);
        if (s.icp) setIcp(s.icp);
      })
      .catch(() => {
        /* keep placeholders */
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    Animated.parallel([
      Animated.timing(fade, { toValue: 1, duration: 900, useNativeDriver: true }),
      Animated.timing(rise, {
        toValue: 0,
        duration: 900,
        easing: Easing.out(Easing.cubic),
        useNativeDriver: true,
      }),
    ]).start();

    Animated.loop(
      Animated.sequence([
        Animated.timing(drift, {
          toValue: 1,
          duration: 10000,
          easing: Easing.inOut(Easing.sin),
          useNativeDriver: true,
        }),
        Animated.timing(drift, {
          toValue: 0,
          duration: 10000,
          easing: Easing.inOut(Easing.sin),
          useNativeDriver: true,
        }),
      ]),
    ).start();
  }, [fade, rise, drift]);

  useEffect(() => {
    const id = setInterval(() => {
      Animated.parallel([
        Animated.timing(tagFade, { toValue: 0, duration: 320, useNativeDriver: true }),
        Animated.timing(tagSlide, { toValue: -10, duration: 320, useNativeDriver: true }),
      ]).start(({ finished }) => {
        if (!finished) return;
        setTagIndex((i) => (i + 1) % TAGLINES.length);
        tagSlide.setValue(12);
        Animated.parallel([
          Animated.timing(tagFade, { toValue: 1, duration: 420, useNativeDriver: true }),
          Animated.timing(tagSlide, {
            toValue: 0,
            duration: 420,
            easing: Easing.out(Easing.cubic),
            useNativeDriver: true,
          }),
        ]).start();
      });
    }, 3400);
    return () => clearInterval(id);
  }, [tagFade, tagSlide]);

  const mistY = drift.interpolate({ inputRange: [0, 1], outputRange: [0, -28] });
  const mistX = drift.interpolate({ inputRange: [0, 1], outputRange: [0, 18] });

  return (
    <View style={styles.root}>
      <LinearGradient
        colors={["#14241F", "#1A2F28", "#2F5348", "#3D6B5A"]}
        locations={[0, 0.35, 0.72, 1]}
        start={{ x: 0.1, y: 0 }}
        end={{ x: 0.9, y: 1 }}
        style={StyleSheet.absoluteFill}
      />
      <Animated.View
        pointerEvents="none"
        style={[styles.mistA, { transform: [{ translateY: mistY }, { translateX: mistX }] }]}
      />
      <Animated.View
        pointerEvents="none"
        style={[
          styles.mistB,
          { transform: [{ translateY: mistY }, { translateX: mistX }, { scaleX: -1 }] },
        ]}
      />

      <SafeAreaView style={styles.safe} edges={["top", "left", "right"]}>
        <Animated.View style={{ flex: 1, opacity: fade, transform: [{ translateY: rise }] }}>
          {/* Top bar */}
          <View style={styles.topBar}>
            <Text style={styles.navBrand} numberOfLines={1}>
              Connect Deeply
            </Text>
            <View style={styles.navActions}>
              <Pressable onPress={() => setLoginOpen(true)} hitSlop={8} style={styles.signInHit}>
                <Text style={styles.signInText}>Sign in</Text>
              </Pressable>
              <Pressable onPress={() => setSignupOpen(true)} style={styles.createBtn}>
                <Text style={styles.createBtnText}>Create account</Text>
              </Pressable>
            </View>
          </View>

          <ScrollView
            contentContainerStyle={[styles.scroll, wide && styles.scrollWide]}
            showsVerticalScrollIndicator={false}
          >
            <View style={[styles.heroRow, wide && styles.heroRowWide]}>
              <View style={[styles.heroCopy, wide && styles.heroCopyWide]}>
                <Text style={styles.kicker}>PRE-MEETING RESEARCH</Text>
                <Text style={[styles.brand, wide && styles.brandWide]}>Connect Deeply</Text>
                <View style={styles.rule} />
                <Animated.Text
                  style={[
                    styles.tagline,
                    { opacity: tagFade, transform: [{ translateY: tagSlide }] },
                  ]}
                >
                  {TAGLINES[tagIndex]}
                </Animated.Text>
                <Text style={styles.support}>
                  Identity-locked research and real common ground before the meeting.
                </Text>
                {userCountDisplay != null ? (
                  <Text style={styles.statLine}>
                    {userCountDisplay} {Number(userCountDisplay.replace(/,/g, "")) === 1 ? "person" : "people"} already researching
                  </Text>
                ) : null}
              </View>

              <BriefingPreview wide={wide} />
            </View>

            <View style={[styles.how, wide && styles.howWide]}>
              <Text style={styles.howTitle}>How it works</Text>
              <Text style={styles.howLead}>Three steps from a name to a conversation that lands.</Text>
              <View style={[styles.steps, wide && styles.stepsWide]}>
                {STEPS.map((s, i) => (
                  <View key={s.title} style={[styles.step, wide && styles.stepWide]}>
                    <Text style={styles.stepIndex}>{String(i + 1).padStart(2, "0")}</Text>
                    <Text style={styles.stepTitle}>{s.title}</Text>
                    <Text style={styles.stepBody}>{s.body}</Text>
                  </View>
                ))}
              </View>
              <Button
                title="Get started"
                variant="ember"
                onPress={() => setSignupOpen(true)}
                style={styles.getStarted}
              />
            </View>

            <View style={[styles.section, wide && styles.howWide]}>
              <Text style={styles.howTitle}>{icp.headline}</Text>
              <Text style={styles.howLead}>{icp.body}</Text>
              <View style={[styles.icpRow, wide && styles.icpRowWide]}>
                {icp.segments.map((seg) => (
                  <Text key={seg} style={styles.icpSeg}>
                    {seg}
                  </Text>
                ))}
              </View>
            </View>

            <View style={[styles.section, wide && styles.howWide]}>
              <Text style={styles.howTitle}>What people say</Text>
              <Text style={styles.howLead}>Early notes from people who prep before they walk in.</Text>
              <View style={[styles.reviews, wide && styles.reviewsWide]}>
                {reviews.map((r) => (
                  <View key={r.id} style={styles.review}>
                    <Text style={styles.reviewQuote}>“{r.quote}”</Text>
                    <Text style={styles.reviewBy}>
                      {r.name}
                      {r.role ? ` · ${r.role}` : ""}
                    </Text>
                  </View>
                ))}
              </View>
            </View>
          </ScrollView>
        </Animated.View>
      </SafeAreaView>

      <AuthModal visible={loginOpen} onClose={() => setLoginOpen(false)} initialMode="login" />
      <SignupSheet visible={signupOpen} onClose={() => setSignupOpen(false)} />
    </View>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: "#14241F" },
  mistA: {
    position: "absolute",
    width: 280,
    height: 420,
    borderRadius: 40,
    backgroundColor: "rgba(232, 160, 122, 0.12)",
    top: "10%",
    right: "-18%",
  },
  mistB: {
    position: "absolute",
    width: 260,
    height: 320,
    borderRadius: 36,
    backgroundColor: "rgba(251, 252, 250, 0.06)",
    bottom: "12%",
    left: "-20%",
  },
  safe: { flex: 1 },
  topBar: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    paddingHorizontal: space.lg,
    paddingTop: 8,
    paddingBottom: 12,
    gap: 12,
  },
  navBrand: {
    fontFamily: fonts.display,
    fontSize: 18,
    color: "#FBFCFA",
    letterSpacing: -0.3,
    flexShrink: 1,
  },
  navActions: {
    flexDirection: "row",
    alignItems: "center",
    gap: 10,
    flexShrink: 0,
  },
  signInHit: {
    paddingVertical: 8,
    paddingHorizontal: 6,
  },
  signInText: {
    fontFamily: fonts.bodySemi,
    fontSize: 14,
    color: "#FBFCFA",
  },
  createBtn: {
    backgroundColor: "#C45C26",
    paddingHorizontal: 14,
    paddingVertical: 10,
    borderRadius: 12,
  },
  createBtnText: {
    fontFamily: fonts.bodySemi,
    fontSize: 13,
    color: "#FBFCFA",
  },
  scroll: {
    paddingHorizontal: space.lg,
    paddingBottom: 48,
  },
  scrollWide: {
    paddingHorizontal: 40,
    maxWidth: 1120,
    width: "100%",
    alignSelf: "center",
  },
  heroRow: {
    marginTop: space.md,
    gap: space.xl,
  },
  heroRowWide: {
    flexDirection: "row",
    alignItems: "stretch",
    gap: 40,
    minHeight: 420,
  },
  heroCopy: {
    flexShrink: 1,
  },
  heroCopyWide: {
    flex: 1,
    justifyContent: "center",
    paddingRight: 12,
    maxWidth: 480,
  },
  kicker: {
    fontFamily: fonts.bodyMed,
    fontSize: 12,
    letterSpacing: 2.4,
    color: "rgba(232, 160, 122, 0.95)",
    marginBottom: 14,
  },
  brand: {
    fontFamily: fonts.display,
    fontSize: 44,
    lineHeight: 50,
    color: "#FBFCFA",
    letterSpacing: -1,
  },
  brandWide: {
    fontSize: 56,
    lineHeight: 62,
  },
  rule: {
    marginTop: 18,
    width: 56,
    height: 2,
    backgroundColor: "rgba(232, 160, 122, 0.75)",
    borderRadius: 1,
  },
  tagline: {
    marginTop: 18,
    fontFamily: fonts.body,
    fontSize: 20,
    lineHeight: 30,
    color: "rgba(251,252,250,0.9)",
    maxWidth: 360,
    minHeight: 60,
  },
  support: {
    marginTop: 12,
    fontFamily: fonts.body,
    fontSize: 15,
    lineHeight: 23,
    color: "rgba(251,252,250,0.62)",
    maxWidth: 360,
  },
  statLine: {
    marginTop: 16,
    fontFamily: fonts.bodySemi,
    fontSize: 14,
    color: "rgba(232, 160, 122, 0.95)",
    letterSpacing: 0.2,
  },
  section: {
    marginTop: space.xxl,
    paddingTop: space.xl,
    borderTopWidth: 1,
    borderTopColor: "rgba(251,252,250,0.12)",
  },
  icpRow: {
    gap: 10,
    marginTop: 4,
  },
  icpRowWide: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 12,
  },
  icpSeg: {
    fontFamily: fonts.bodyMed,
    fontSize: 14,
    color: "rgba(251,252,250,0.78)",
    borderLeftWidth: 2,
    borderLeftColor: "rgba(232, 160, 122, 0.65)",
    paddingLeft: 10,
    paddingVertical: 4,
  },
  reviews: {
    gap: space.lg,
  },
  reviewsWide: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 24,
  },
  review: {
    flexGrow: 1,
    flexBasis: 260,
    maxWidth: 340,
  },
  reviewQuote: {
    fontFamily: fonts.body,
    fontSize: 16,
    lineHeight: 24,
    color: "rgba(251,252,250,0.88)",
  },
  reviewBy: {
    marginTop: 10,
    fontFamily: fonts.bodyMed,
    fontSize: 13,
    color: "rgba(232, 160, 122, 0.9)",
  },
  previewShell: {
    position: "relative",
    marginTop: 4,
  },
  previewShellWide: {
    flex: 1.15,
    marginTop: 0,
    justifyContent: "center",
  },
  previewGlow: {
    ...StyleSheet.absoluteFillObject,
    backgroundColor: "rgba(232, 160, 122, 0.35)",
    borderRadius: 20,
    transform: [{ scale: 1.02 }],
  },
  previewPaper: {
    backgroundColor: "#FBFCFA",
    borderRadius: 18,
    paddingVertical: 22,
    paddingHorizontal: 22,
    borderWidth: 1,
    borderColor: "rgba(18, 32, 28, 0.08)",
    minHeight: 280,
  },
  previewEyebrow: {
    fontFamily: fonts.bodyMed,
    fontSize: 11,
    letterSpacing: 1.6,
    color: "#3D6B5A",
    textTransform: "uppercase",
  },
  previewName: {
    fontFamily: fonts.display,
    fontSize: 26,
    lineHeight: 32,
    color: "#12201C",
    letterSpacing: -0.4,
  },
  previewMeta: {
    fontFamily: fonts.body,
    fontSize: 14,
    color: "rgba(18, 32, 28, 0.55)",
  },
  previewSection: {
    fontFamily: fonts.bodySemi,
    fontSize: 12,
    letterSpacing: 1.2,
    textTransform: "uppercase",
    color: "#2F4F44",
  },
  previewHook: {
    fontFamily: fonts.body,
    fontSize: 15,
    lineHeight: 22,
    color: "#12201C",
  },
  previewOpener: {
    fontFamily: fonts.bodyMed,
    fontSize: 15,
    lineHeight: 23,
    color: "#1A2F28",
    fontStyle: "italic",
  },
  how: {
    marginTop: space.xxl,
    paddingTop: space.xl,
    borderTopWidth: 1,
    borderTopColor: "rgba(251,252,250,0.12)",
  },
  howWide: {
    marginTop: 56,
  },
  howTitle: {
    fontFamily: fonts.display,
    fontSize: 28,
    color: "#FBFCFA",
    letterSpacing: -0.4,
  },
  howLead: {
    marginTop: 8,
    fontFamily: fonts.body,
    fontSize: 16,
    lineHeight: 24,
    color: "rgba(251,252,250,0.68)",
    maxWidth: 420,
    marginBottom: space.lg,
  },
  steps: {
    gap: space.lg,
  },
  stepsWide: {
    flexDirection: "row",
    gap: 28,
  },
  step: {
    paddingBottom: 4,
  },
  stepWide: {
    flex: 1,
  },
  stepIndex: {
    fontFamily: fonts.bodyMed,
    fontSize: 12,
    letterSpacing: 1.4,
    color: "rgba(232, 160, 122, 0.9)",
    marginBottom: 6,
  },
  stepTitle: {
    fontFamily: fonts.bodySemi,
    fontSize: 18,
    color: "#FBFCFA",
    marginBottom: 6,
  },
  stepBody: {
    fontFamily: fonts.body,
    fontSize: 14,
    lineHeight: 21,
    color: "rgba(251,252,250,0.68)",
  },
  getStarted: {
    marginTop: space.xl,
    alignSelf: "flex-start",
    minWidth: 180,
  },
});

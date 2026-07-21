import { LinearGradient } from "expo-linear-gradient";
import React, { useEffect, useRef, useState } from "react";
import { Animated, Easing, StyleSheet, Text, View } from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { AuthModal } from "../../components/AuthModal";
import { SignupSheet } from "../../components/SignupSheet";
import { Button } from "../../components/ui";
import { fonts, space } from "../../lib/theme";

const TAGLINES = [
  "Know what you share before you say hello.",
  "Walk into the room already connected.",
  "Real overlap — not generic small talk.",
  "Research once. Open with something true.",
];

export default function Welcome() {
  const fade = useRef(new Animated.Value(0)).current;
  const rise = useRef(new Animated.Value(24)).current;
  const brandPulse = useRef(new Animated.Value(0)).current;
  const tagFade = useRef(new Animated.Value(1)).current;
  const tagSlide = useRef(new Animated.Value(0)).current;
  const drift = useRef(new Animated.Value(0)).current;
  const [tagIndex, setTagIndex] = useState(0);
  const [loginOpen, setLoginOpen] = useState(false);
  const [signupOpen, setSignupOpen] = useState(false);

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
        Animated.timing(brandPulse, {
          toValue: 1,
          duration: 2200,
          easing: Easing.inOut(Easing.sin),
          useNativeDriver: true,
        }),
        Animated.timing(brandPulse, {
          toValue: 0,
          duration: 2200,
          easing: Easing.inOut(Easing.sin),
          useNativeDriver: true,
        }),
      ]),
    ).start();

    Animated.loop(
      Animated.sequence([
        Animated.timing(drift, {
          toValue: 1,
          duration: 9000,
          easing: Easing.inOut(Easing.sin),
          useNativeDriver: true,
        }),
        Animated.timing(drift, {
          toValue: 0,
          duration: 9000,
          easing: Easing.inOut(Easing.sin),
          useNativeDriver: true,
        }),
      ]),
    ).start();
  }, [fade, rise, brandPulse, drift]);

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

  const brandOpacity = brandPulse.interpolate({
    inputRange: [0, 1],
    outputRange: [0.92, 1],
  });
  const brandScale = brandPulse.interpolate({
    inputRange: [0, 1],
    outputRange: [1, 1.015],
  });
  const mistY = drift.interpolate({
    inputRange: [0, 1],
    outputRange: [0, -28],
  });
  const mistX = drift.interpolate({
    inputRange: [0, 1],
    outputRange: [0, 18],
  });

  return (
    <View style={styles.root}>
      <LinearGradient
        colors={["#14241F", "#1A2F28", "#2F5348", "#3D6B5A"]}
        locations={[0, 0.35, 0.72, 1]}
        start={{ x: 0.1, y: 0 }}
        end={{ x: 0.9, y: 1 }}
        style={StyleSheet.absoluteFill}
      />
      {/* Soft drifting light washes — not an empty centered disc */}
      <Animated.View
        pointerEvents="none"
        style={[
          styles.mistA,
          { transform: [{ translateY: mistY }, { translateX: mistX }] },
        ]}
      />
      <Animated.View
        pointerEvents="none"
        style={[
          styles.mistB,
          {
            transform: [{ translateY: mistY }, { translateX: mistX }, { scaleX: -1 }],
          },
        ]}
      />

      <SafeAreaView style={styles.safe}>
        <Animated.View
          style={{
            opacity: fade,
            transform: [{ translateY: rise }],
            flex: 1,
            justifyContent: "space-between",
          }}
        >
          <View style={{ marginTop: space.xxl }}>
            <Text style={styles.kicker}>PRE-MEETING RESEARCH</Text>
            <Animated.Text
              style={[
                styles.brand,
                { opacity: brandOpacity, transform: [{ scale: brandScale }] },
              ]}
            >
              Connect Deeply
            </Animated.Text>
            <View style={styles.rule} />
            <Animated.Text
              style={[
                styles.tagline,
                { opacity: tagFade, transform: [{ translateY: tagSlide }] },
              ]}
            >
              {TAGLINES[tagIndex]}
            </Animated.Text>
          </View>

          <View style={{ gap: 12, marginBottom: space.lg }}>
            <Button title="Create account" onPress={() => setSignupOpen(true)} variant="ember" />
            <Button title="Sign in" onPress={() => setLoginOpen(true)} variant="ghost" style={styles.ghostOnDark} />
          </View>
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
    top: "12%",
    right: "-18%",
    transform: [{ rotate: "18deg" }],
  },
  mistB: {
    position: "absolute",
    width: 260,
    height: 320,
    borderRadius: 36,
    backgroundColor: "rgba(251, 252, 250, 0.06)",
    bottom: "18%",
    left: "-20%",
    transform: [{ rotate: "-12deg" }],
  },
  safe: { flex: 1, paddingHorizontal: space.lg },
  kicker: {
    fontFamily: fonts.bodyMed,
    fontSize: 12,
    letterSpacing: 2.4,
    color: "rgba(232, 160, 122, 0.92)",
    marginBottom: 14,
  },
  brand: {
    fontFamily: fonts.display,
    fontSize: 48,
    lineHeight: 54,
    color: "#FBFCFA",
    letterSpacing: -1,
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
    color: "rgba(251,252,250,0.86)",
    maxWidth: 320,
    minHeight: 90,
  },
  ghostOnDark: {
    borderColor: "rgba(251,252,250,0.28)",
  },
});

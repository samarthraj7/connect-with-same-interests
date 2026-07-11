import { LinearGradient } from "expo-linear-gradient";
import { useRouter } from "expo-router";
import React, { useEffect } from "react";
import { Animated, StyleSheet, Text, View } from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { Button } from "../../components/ui";
import { colors, fonts, space } from "../../lib/theme";

export default function Welcome() {
  const router = useRouter();
  const fade = React.useRef(new Animated.Value(0)).current;
  const rise = React.useRef(new Animated.Value(18)).current;

  useEffect(() => {
    Animated.parallel([
      Animated.timing(fade, { toValue: 1, duration: 700, useNativeDriver: true }),
      Animated.timing(rise, { toValue: 0, duration: 700, useNativeDriver: true }),
    ]).start();
  }, [fade, rise]);

  return (
    <View style={styles.root}>
      <LinearGradient
        colors={["#1A2F28", "#243F36", "#3D6B5A"]}
        start={{ x: 0.2, y: 0 }}
        end={{ x: 0.8, y: 1 }}
        style={StyleSheet.absoluteFill}
      />
      <View style={styles.glow} />
      <SafeAreaView style={styles.safe}>
        <Animated.View style={{ opacity: fade, transform: [{ translateY: rise }], flex: 1, justifyContent: "space-between" }}>
          <View style={{ marginTop: space.xxl }}>
            <Text style={styles.brand}>Connect Deeply</Text>
            <Text style={styles.line}>Know what you share before you say hello.</Text>
          </View>
          <View style={{ gap: 12, marginBottom: space.lg }}>
            <Button title="Create account" onPress={() => router.push("/(auth)/signup")} variant="ember" />
            <Button title="Sign in" onPress={() => router.push("/(auth)/login")} variant="ghost" style={styles.ghostOnDark} />
          </View>
        </Animated.View>
      </SafeAreaView>
    </View>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: colors.forest },
  glow: {
    position: "absolute",
    width: 320,
    height: 320,
    borderRadius: 160,
    backgroundColor: "rgba(232, 160, 122, 0.18)",
    top: "28%",
    alignSelf: "center",
  },
  safe: { flex: 1, paddingHorizontal: space.lg },
  brand: {
    fontFamily: fonts.display,
    fontSize: 48,
    lineHeight: 54,
    color: colors.chalk,
    letterSpacing: -1,
  },
  line: {
    marginTop: 14,
    fontFamily: fonts.body,
    fontSize: 18,
    lineHeight: 26,
    color: "rgba(251,252,250,0.78)",
    maxWidth: 300,
  },
  ghostOnDark: {
    borderColor: "rgba(251,252,250,0.28)",
  },
});

import { LinearGradient } from "expo-linear-gradient";
import React from "react";
import { StyleSheet, View, ViewStyle } from "react-native";
import { useTheme } from "../lib/theme-context";

/** Soft atmospheric wash — themed for light/dark. */
export function ScreenBackdrop({
  children,
  style,
}: {
  children: React.ReactNode;
  style?: ViewStyle;
}) {
  const { colors } = useTheme();
  return (
    <View style={[styles.root, style]}>
      <LinearGradient
        colors={colors.gradient}
        start={{ x: 0.1, y: 0 }}
        end={{ x: 0.9, y: 1 }}
        style={StyleSheet.absoluteFill}
      />
      <View style={[styles.orbA, { backgroundColor: colors.orbA }]} />
      <View style={[styles.orbB, { backgroundColor: colors.orbB }]} />
      {children}
    </View>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1 },
  orbA: {
    position: "absolute",
    width: 280,
    height: 280,
    borderRadius: 140,
    top: -60,
    right: -80,
  },
  orbB: {
    position: "absolute",
    width: 220,
    height: 220,
    borderRadius: 110,
    bottom: 80,
    left: -70,
  },
});

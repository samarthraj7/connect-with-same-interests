import { useRouter } from "expo-router";
import React, { useState } from "react";
import { KeyboardAvoidingView, Platform, ScrollView, StyleSheet, Text, View } from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { ScreenBackdrop } from "../../components/ScreenBackdrop";
import { BrandMark, Button, Field } from "../../components/ui";
import { api } from "../../lib/api";
import { useAuth } from "../../lib/auth";
import { fonts, space } from "../../lib/theme";
import { useTheme } from "../../lib/theme-context";

/** Full-page login fallback; welcome uses AuthModal for the primary path. */
export default function Login() {
  const router = useRouter();
  const { setSession } = useAuth();
  const { colors } = useTheme();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  const onSubmit = async () => {
    setError("");
    setLoading(true);
    try {
      const res = await api.login(email.trim(), password);
      await setSession(res.token, res.user);
      router.replace("/(app)/home");
    } catch (e: any) {
      setError(e.message || "Login failed");
    } finally {
      setLoading(false);
    }
  };

  return (
    <ScreenBackdrop>
      <SafeAreaView style={{ flex: 1 }}>
        <KeyboardAvoidingView behavior={Platform.OS === "ios" ? "padding" : undefined} style={{ flex: 1 }}>
          <ScrollView contentContainerStyle={styles.pad} keyboardShouldPersistTaps="handled">
            <BrandMark />
            <Text style={{ fontFamily: fonts.body, color: colors.muted, marginVertical: space.lg, fontSize: 16 }}>
              Welcome back.
            </Text>
            <Field label="Email" autoCapitalize="none" keyboardType="email-address" value={email} onChangeText={setEmail} />
            <Field label="Password" secureTextEntry value={password} onChangeText={setPassword} />
            {error ? <Text style={{ color: colors.danger, fontFamily: fonts.bodyMed, marginBottom: 12 }}>{error}</Text> : null}
            <Button title="Sign in" onPress={onSubmit} loading={loading} />
            <Button title="Back" variant="ghost" onPress={() => router.back()} style={{ marginTop: 10 }} />
          </ScrollView>
        </KeyboardAvoidingView>
      </SafeAreaView>
    </ScreenBackdrop>
  );
}

const styles = StyleSheet.create({
  pad: { padding: space.lg, paddingTop: space.xl },
});

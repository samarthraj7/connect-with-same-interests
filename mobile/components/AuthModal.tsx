import React, { useState } from "react";
import {
  Image,
  KeyboardAvoidingView,
  Modal,
  Platform,
  Pressable,
  StyleSheet,
  Text,
  View,
} from "react-native";
import { api, getLastUserPhoto } from "../lib/api";
import { useAuth } from "../lib/auth";
import { useTheme } from "../lib/theme-context";
import { fonts, space } from "../lib/theme";
import { Button, Field } from "./ui";

type Props = {
  visible: boolean;
  onClose: () => void;
  initialMode?: "login" | "signup";
  onSuccess?: () => void;
};

/** Compact auth sheet — login (and short signup hop) without taking the full page. */
export function AuthModal({ visible, onClose, initialMode = "login", onSuccess }: Props) {
  const { colors } = useTheme();
  const { setSession } = useAuth();
  const [mode, setMode] = useState<"login" | "signup">(initialMode);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [name, setName] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [photoUrl, setPhotoUrl] = useState<string | null>(null);

  React.useEffect(() => {
    if (visible) {
      setMode(initialMode);
      setError("");
      getLastUserPhoto()
        .then((u) => setPhotoUrl(u))
        .catch(() => setPhotoUrl(null));
    }
  }, [visible, initialMode]);

  const submit = async () => {
    setError("");
    setLoading(true);
    try {
      if (mode === "login") {
        const res = await api.login(email.trim(), password);
        await setSession(res.token, res.user);
      } else {
        if (!name.trim() || password.length < 6) {
          throw new Error("Name and password (6+ chars) required.");
        }
        const res = await api.signup({
          name: name.trim(),
          email: email.trim(),
          password,
          research_me: false,
        });
        await setSession(res.token, res.user);
      }
      onSuccess?.();
      onClose();
    } catch (e: any) {
      setError(e.message || "Failed");
    } finally {
      setLoading(false);
    }
  };

  return (
    <Modal visible={visible} transparent animationType="fade" onRequestClose={onClose}>
      <Pressable style={[styles.backdrop, { backgroundColor: "rgba(0,0,0,0.55)" }]} onPress={onClose}>
        <KeyboardAvoidingView behavior={Platform.OS === "ios" ? "padding" : undefined}>
          <Pressable
            style={[styles.card, { backgroundColor: colors.chalk, borderColor: colors.line }]}
            onPress={(e) => e.stopPropagation()}
          >
            {mode === "login" && photoUrl ? (
              <Image source={{ uri: photoUrl }} style={styles.avatar} />
            ) : null}
            <Text style={[styles.title, { color: colors.ink }]}>
              {mode === "login" ? "Sign in" : "Quick account"}
            </Text>
            <Text style={[styles.sub, { color: colors.muted }]}>
              {mode === "login"
                ? "Welcome back."
                : "Email + name first. Full profile research is on Create account."}
            </Text>
            {mode === "signup" ? (
              <Field label="Full name" value={name} onChangeText={setName} />
            ) : null}
            <Field
              label="Email"
              autoCapitalize="none"
              keyboardType="email-address"
              value={email}
              onChangeText={setEmail}
            />
            <Field label="Password" secureTextEntry value={password} onChangeText={setPassword} />
            {error ? <Text style={{ color: colors.danger, marginBottom: 8 }}>{error}</Text> : null}
            <Button
              title={loading ? "…" : mode === "login" ? "Sign in" : "Create"}
              onPress={submit}
              loading={loading}
            />
            <Pressable
              onPress={() => setMode(mode === "login" ? "signup" : "login")}
              style={{ marginTop: 12 }}
            >
              <Text style={{ fontFamily: fonts.bodyMed, color: colors.ember, textAlign: "center" }}>
                {mode === "login" ? "Need an account? Quick signup" : "Have an account? Sign in"}
              </Text>
            </Pressable>
          </Pressable>
        </KeyboardAvoidingView>
      </Pressable>
    </Modal>
  );
}

const styles = StyleSheet.create({
  backdrop: {
    flex: 1,
    justifyContent: "center",
    padding: space.lg,
  },
  card: {
    borderRadius: 20,
    borderWidth: 1,
    padding: space.lg,
    maxWidth: 420,
    alignSelf: "center",
    width: "100%",
  },
  avatar: {
    width: 72,
    height: 72,
    borderRadius: 36,
    alignSelf: "center",
    marginBottom: 12,
    backgroundColor: "#ddd",
  },
  title: { fontFamily: fonts.display, fontSize: 28, marginBottom: 6 },
  sub: { fontFamily: fonts.body, fontSize: 14, marginBottom: space.md, lineHeight: 20 },
});

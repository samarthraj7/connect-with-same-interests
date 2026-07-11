import { useRouter } from "expo-router";
import React, { useState } from "react";
import {
  KeyboardAvoidingView,
  Platform,
  ScrollView,
  StyleSheet,
  Text,
  View,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { ScreenBackdrop } from "../../components/ScreenBackdrop";
import { BrandMark, Button, ChipInput, Field } from "../../components/ui";
import { api } from "../../lib/api";
import { useAuth } from "../../lib/auth";
import { colors, fonts, space } from "../../lib/theme";

/**
 * Signup collects hobbies / interests / sports up front so overlap quality
 * does not depend on scraping the searcher's socials. When someone you
 * research later joins the app, the same fields become their public social
 * signal for mutual overlap.
 */
export default function Signup() {
  const router = useRouter();
  const { setSession } = useAuth();
  const [step, setStep] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [headline, setHeadline] = useState("");
  const [location, setLocation] = useState("");
  const [linkedin, setLinkedin] = useState("");
  const [hobbies, setHobbies] = useState<string[]>([]);
  const [interests, setInterests] = useState<string[]>([]);
  const [sports, setSports] = useState<string[]>([]);

  const next = () => {
    setError("");
    if (step === 0) {
      if (!name.trim() || !email.trim() || password.length < 6) {
        setError("Name, email, and a password (6+ chars) are required.");
        return;
      }
    }
    setStep((s) => Math.min(s + 1, 2));
  };

  const submit = async () => {
    setError("");
    setLoading(true);
    try {
      const res = await api.signup({
        name: name.trim(),
        email: email.trim(),
        password,
        headline: headline.trim(),
        location: location.trim(),
        linkedin_url: linkedin.trim(),
        hobbies,
        interests,
        sports,
        talking_goals: [
          "Find genuine common ground before meetings",
          "Open warm, specific conversations",
        ],
      });
      await setSession(res.token, res.user);
      router.replace("/(app)/home");
    } catch (e: any) {
      setError(e.message || "Signup failed");
    } finally {
      setLoading(false);
    }
  };

  return (
    <ScreenBackdrop>
      <SafeAreaView style={{ flex: 1 }}>
        <KeyboardAvoidingView behavior={Platform.OS === "ios" ? "padding" : undefined} style={{ flex: 1 }}>
          <ScrollView contentContainerStyle={styles.pad} keyboardShouldPersistTaps="handled">
            <BrandMark size="sm" />
            <Text style={styles.step}>Step {step + 1} of 3</Text>
            <Text style={styles.title}>
              {step === 0 ? "Who you are" : step === 1 ? "Where you work" : "What you’re into"}
            </Text>
            <Text style={styles.sub}>
              {step === 2
                ? "Hobbies and interests power better overlap — collected here at signup, not scraped."
                : "A few details now. You can refine them anytime."}
            </Text>

            {step === 0 && (
              <View>
                <Field label="Full name" value={name} onChangeText={setName} />
                <Field label="Email" autoCapitalize="none" keyboardType="email-address" value={email} onChangeText={setEmail} />
                <Field label="Password" secureTextEntry value={password} onChangeText={setPassword} />
              </View>
            )}
            {step === 1 && (
              <View>
                <Field label="Headline" value={headline} onChangeText={setHeadline} placeholder="Engineer · Founder · Investor" />
                <Field label="Location" value={location} onChangeText={setLocation} placeholder="Los Angeles, CA" />
                <Field label="LinkedIn URL" autoCapitalize="none" value={linkedin} onChangeText={setLinkedin} placeholder="https://linkedin.com/in/..." />
              </View>
            )}
            {step === 2 && (
              <View>
                <ChipInput label="Hobbies" value={hobbies} onChange={setHobbies} placeholder="e.g. hiking, pottery" />
                <ChipInput label="Interests" value={interests} onChange={setInterests} placeholder="e.g. climate tech, angel investing" />
                <ChipInput label="Sports" value={sports} onChange={setSports} placeholder="e.g. tennis, cricket" />
              </View>
            )}

            {error ? <Text style={styles.err}>{error}</Text> : null}

            {step < 2 ? (
              <Button title="Continue" onPress={next} />
            ) : (
              <Button title="Start connecting" onPress={submit} loading={loading} variant="ember" />
            )}
            <Button
              title={step === 0 ? "Back" : "Previous"}
              variant="ghost"
              onPress={() => (step === 0 ? router.back() : setStep((s) => s - 1))}
              style={{ marginTop: 10 }}
            />
          </ScrollView>
        </KeyboardAvoidingView>
      </SafeAreaView>
    </ScreenBackdrop>
  );
}

const styles = StyleSheet.create({
  pad: { padding: space.lg, paddingBottom: 48 },
  step: {
    marginTop: space.lg,
    fontFamily: fonts.bodyMed,
    fontSize: 12,
    letterSpacing: 1,
    textTransform: "uppercase",
    color: colors.leaf,
  },
  title: {
    fontFamily: fonts.display,
    fontSize: 32,
    color: colors.ink,
    marginTop: 6,
    letterSpacing: -0.5,
  },
  sub: {
    fontFamily: fonts.body,
    fontSize: 15,
    lineHeight: 22,
    color: colors.muted,
    marginVertical: space.md,
  },
  err: { color: colors.danger, fontFamily: fonts.bodyMed, marginBottom: 12 },
});

import { Redirect } from "expo-router";

/** Full-page signup retired — use welcome popup (SignupSheet). */
export default function SignupRedirect() {
  return <Redirect href="/(auth)/welcome" />;
}

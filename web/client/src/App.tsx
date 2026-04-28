import { Router, Route, Switch } from "wouter";
import { useHashLocation } from "wouter/use-hash-location";
import { QueryClientProvider } from "@tanstack/react-query";
import { queryClient } from "@/lib/queryClient";
import { Toaster } from "@/components/ui/toaster";
import { TooltipProvider } from "@/components/ui/tooltip";

import { Shell } from "@/components/Shell";
import { AuthGate } from "@/pages/AuthGate";
import Chat from "@/pages/Chat";
import Journal from "@/pages/Journal";
import Data from "@/pages/Data";
import Settings from "@/pages/Settings";
import NotFound from "@/pages/not-found";

function AppRouter() {
  return (
    <Switch>
      <Route path="/" component={Chat} />
      <Route path="/journal" component={Journal} />
      <Route path="/data" component={Data} />
      <Route path="/settings" component={Settings} />
      <Route component={NotFound} />
    </Switch>
  );
}

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <TooltipProvider>
        <Toaster />
        <AuthGate>
          <Router hook={useHashLocation}>
            <Shell>
              <AppRouter />
            </Shell>
          </Router>
        </AuthGate>
      </TooltipProvider>
    </QueryClientProvider>
  );
}

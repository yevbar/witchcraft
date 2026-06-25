// ForgeHeadless.java — prove Forge runs headless here: a full AI-vs-AI game, no GUI.
//
// Built from the canonical headless setup in Forge's own AITest (GuiBase.setInterface(new GuiDesktop())
// + FModel.initialize) and the deck/match wiring in SimulateMatch / mtg's BattleHarness. This is
// step 1 of wiring our Python engine in as a player: first confirm a headless Forge game completes here.
//
// Compile:  javac -cp $FATJAR -d out forge_integration/ForgeHeadless.java
// Run:      java -Djava.awt.headless=true -cp $FATJAR:out ForgeHeadless
//   (cwd = the forge tree root so relative assets resolve; assets dir is overridden below to be safe.)

import com.google.common.collect.Lists;

import forge.GuiDesktop;
import forge.ai.LobbyPlayerAi;
import forge.deck.Deck;
import forge.game.Game;
import forge.game.GameRules;
import forge.game.GameType;
import forge.game.Match;
import forge.game.player.RegisteredPlayer;
import forge.gui.GuiBase;
import forge.item.PaperCard;
import forge.localinstance.properties.ForgePreferences.FPref;
import forge.model.FModel;

import java.io.File;
import java.util.List;

public class ForgeHeadless {
    static final String ASSETS = System.getenv().getOrDefault("FORGE_ASSETS",
            new File("forge-gui").getAbsolutePath() + File.separator);

    static void initForge() {
        GuiBase.setInterface(new GuiDesktop() {
            @Override public String getAssetsDir() { return ASSETS; }
        });
        FModel.initialize(null, prefs -> {
            prefs.setPref(FPref.LOAD_CARD_SCRIPTS_LAZILY, false);
            prefs.setPref(FPref.UI_LANGUAGE, "en-US");
            return null;
        });
    }

    static PaperCard card(String name) {
        PaperCard c = FModel.getMagicDb().getCommonCards().getCard(name);
        if (c == null) throw new RuntimeException("card not found in Forge db: " + name);
        return c;
    }

    static Deck gruulDeck(String name) {
        Deck d = new Deck(name);
        d.getMain().add(card("Forest"), 30);
        d.getMain().add(card("Mountain"), 14);
        d.getMain().add(card("Grizzly Bears"), 4);
        d.getMain().add(card("Gray Ogre"), 4);
        d.getMain().add(card("Hill Giant"), 4);
        d.getMain().add(card("Craw Wurm"), 4);
        return d; // 60 cards
    }

    public static void main(String[] args) {
        initForge();
        System.out.println("Forge initialized headlessly. Card DB loaded: "
                + (FModel.getMagicDb().getCommonCards().getCard("Forest") != null));

        List<RegisteredPlayer> players = Lists.newArrayList();
        players.add(new RegisteredPlayer(gruulDeck("Gruul-A")).setPlayer(new LobbyPlayerAi("Forge-AI-1", null)));
        players.add(new RegisteredPlayer(gruulDeck("Gruul-B")).setPlayer(new LobbyPlayerAi("Forge-AI-2", null)));

        GameRules rules = new GameRules(GameType.Constructed);
        rules.setGamesPerMatch(1);
        Match match = new Match(rules, players, "headless-aivai");
        Game game = new Game(players, rules, match);

        System.out.println("Starting AI-vs-AI game...");
        long t0 = System.currentTimeMillis();
        match.startGame(game);
        long ms = System.currentTimeMillis() - t0;

        String winner = (game.getOutcome() != null && game.getOutcome().getWinningLobbyPlayer() != null)
                ? game.getOutcome().getWinningLobbyPlayer().getName() : "draw/none";
        System.out.println("RESULT winner=" + winner
                + " turns=" + game.getPhaseHandler().getTurn() + " wall=" + ms + "ms");
    }
}

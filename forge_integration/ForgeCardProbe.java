// ForgeCardProbe.java — does THIS Forge build know every card in our 4 cEDH decks? Reads a newline-delimited
// card-name list (-Dcards=/path) and prints, for each, whether Forge's card DB has it. The gating check before
// building the Commander tournament: a deck with a card Forge can't load can't be seated.
import java.nio.file.Files;
import java.nio.file.Paths;
import java.util.List;

import forge.gui.GuiBase;
import forge.GuiDesktop;
import forge.localinstance.properties.ForgePreferences.FPref;
import forge.model.FModel;
import forge.item.PaperCard;

public class ForgeCardProbe {
    public static void main(String[] args) throws Exception {
        String assets = System.getProperty("assets", "/home/zucc/Development/witchcraft/forge/forge-gui/");
        GuiBase.setInterface(new GuiDesktop() { @Override public String getAssetsDir() { return assets; } });
        FModel.initialize(null, prefs -> {
            prefs.setPref(FPref.LOAD_CARD_SCRIPTS_LAZILY, false);
            prefs.setPref(FPref.UI_LANGUAGE, "en-US");
            return null;
        });
        List<String> names = Files.readAllLines(Paths.get(System.getProperty("cards")));
        int ok = 0, miss = 0;
        for (String n : names) {
            n = n.trim();
            if (n.isEmpty()) continue;
            PaperCard c = FModel.getMagicDb().getCommonCards().getCard(n);
            if (c == null) {
                // try the front face of a DFC ("A // B" -> "A")
                String front = n.contains("//") ? n.split("//")[0].trim() : n;
                c = FModel.getMagicDb().getCommonCards().getCard(front);
            }
            if (c == null) { System.out.println("MISSING\t" + n); miss++; }
            else ok++;
        }
        System.out.println("PROBE ok=" + ok + " missing=" + miss);
    }
}

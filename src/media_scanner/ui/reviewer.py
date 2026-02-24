"""Interactive duplicate review UI."""

from __future__ import annotations

from rich.panel import Panel
from rich.text import Text

from media_scanner.config import Config
from media_scanner.core.quality_scorer import rank_group, score_item
from media_scanner.data.models import ActionRecord, ActionType, DuplicateGroup
from media_scanner.ui.console import console
from media_scanner.ui.tables import duplicate_group_table


class ReviewSession:
    """Interactive review session for duplicate groups."""

    def __init__(
        self,
        groups: list[DuplicateGroup],
        config: Config,
    ) -> None:
        self.groups = groups
        self.config = config
        self.actions: list[ActionRecord] = []
        self.current_index = 0
        self.history: list[int] = []  # for undo

    def run(self) -> list[ActionRecord]:
        """Run the interactive review loop. Returns list of actions."""
        if not self.groups:
            console.print("[yellow]No duplicate groups to review.[/yellow]")
            return []

        console.print(
            f"\n[bold]Starting review of {len(self.groups)} duplicate groups.[/bold]\n"
            "Commands: [a]ccept recommendation, [c]hoose keeper, "
            "[k]eep all, [s]kip, [u]ndo, [q]uit\n"
        )

        while 0 <= self.current_index < len(self.groups):
            group = self.groups[self.current_index]
            group = rank_group(group, self.config)

            # Compute scores for display
            scores = {
                item.uuid: score_item(item, group, self.config)
                for item in group.items
            }

            # Show the group
            table = duplicate_group_table(
                group,
                self.current_index + 1,
                len(self.groups),
                scores=scores,
            )
            console.print(table)

            # Show metadata summary
            keeper = group.items[0]
            meta_parts = []
            if keeper.has_gps:
                meta_parts.append("GPS: Yes")
            if keeper.persons:
                meta_parts.append(f"Faces: {', '.join(keeper.persons[:3])}")
            if keeper.albums:
                meta_parts.append(f"Album: {', '.join(keeper.albums[:2])}")
            if meta_parts:
                console.print(f"  {' | '.join(meta_parts)}")

            console.print(
                f"  [green]Recommendation: Keep #{1} "
                f"({keeper.filename}, score {scores[keeper.uuid]:.2f})[/green]"
            )

            # Get user input
            action = self._get_action(group)
            if action == "quit":
                break
            elif action == "undo":
                self._undo()
            else:
                self.current_index += 1

        console.print(
            f"\n[bold]Review complete. {len(self.actions)} actions recorded.[/bold]"
        )
        return self.actions

    def _get_action(self, group: DuplicateGroup) -> str:
        while True:
            try:
                choice = console.input(
                    "\n  [bold][a]ccept [c]hoose [k]eep all [s]kip [u]ndo [q]uit:[/bold] "
                ).strip().lower()
            except (EOFError, KeyboardInterrupt):
                return "quit"

            if choice in ("a", "accept"):
                self._accept_recommendation(group)
                return "accept"
            elif choice in ("c", "choose"):
                self._choose_keeper(group)
                return "choose"
            elif choice in ("k", "keep", "keep all"):
                self._keep_all(group)
                return "keep"
            elif choice in ("s", "skip"):
                return "skip"
            elif choice in ("u", "undo"):
                return "undo"
            elif choice in ("q", "quit"):
                return "quit"
            else:
                console.print("[red]Invalid choice. Try again.[/red]")

    def _accept_recommendation(self, group: DuplicateGroup) -> None:
        """Accept the automatic recommendation: keep #1, delete the rest."""
        self.history.append(self.current_index)
        for item in group.items:
            if item.uuid == group.recommended_keep_uuid:
                self.actions.append(
                    ActionRecord(uuid=item.uuid, action=ActionType.KEEP, group_id=group.group_id)
                )
            else:
                self.actions.append(
                    ActionRecord(uuid=item.uuid, action=ActionType.DELETE, group_id=group.group_id)
                )

    def _choose_keeper(self, group: DuplicateGroup) -> None:
        """Let the user pick which item to keep."""
        try:
            choice = console.input(
                f"  Enter number to keep (1-{len(group.items)}): "
            ).strip()
            idx = int(choice) - 1
            if 0 <= idx < len(group.items):
                self.history.append(self.current_index)
                keeper_uuid = group.items[idx].uuid
                for item in group.items:
                    if item.uuid == keeper_uuid:
                        self.actions.append(
                            ActionRecord(uuid=item.uuid, action=ActionType.KEEP, group_id=group.group_id)
                        )
                    else:
                        self.actions.append(
                            ActionRecord(uuid=item.uuid, action=ActionType.DELETE, group_id=group.group_id)
                        )
            else:
                console.print("[red]Invalid number.[/red]")
        except (ValueError, EOFError, KeyboardInterrupt):
            console.print("[red]Invalid input.[/red]")

    def _keep_all(self, group: DuplicateGroup) -> None:
        """Mark all items in the group as keep."""
        self.history.append(self.current_index)
        for item in group.items:
            self.actions.append(
                ActionRecord(uuid=item.uuid, action=ActionType.KEEP, group_id=group.group_id)
            )

    def _undo(self) -> None:
        """Undo the last action."""
        if not self.history:
            console.print("[yellow]Nothing to undo.[/yellow]")
            return
        prev_index = self.history.pop()
        # Remove actions from the undone group
        if self.actions:
            group = self.groups[prev_index]
            group_uuids = {item.uuid for item in group.items}
            self.actions = [a for a in self.actions if a.uuid not in group_uuids]
        self.current_index = prev_index
        console.print("[yellow]Undone. Showing previous group again.[/yellow]")

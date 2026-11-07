import 'dart:async';

import 'package:flutter/material.dart';
import 'package:calories_tracker/core/theme/app_colors.dart';
import 'package:calories_tracker/core/theme/app_spacing.dart';
import 'package:calories_tracker/features/food_log/data/food_log_api_client.dart';

/// Step 2: "Is this correct?" Every item shows where its numbers came from
/// (database = verified, AI = estimate). The user can change the amount,
/// pick a different dish ("Not this?"), use the AI estimate, remove an item
/// or add a missed one. Each fix is sent to /fix, which recalculates
/// nutrients and records the correction for learning.
class FoodReviewScreen extends StatefulWidget {
  const FoodReviewScreen({
    super.key,
    required this.analysis,
    required this.phoneNumber,
    required this.accessToken,
    this.mealType,
  });

  final FoodAnalysis analysis;
  final String phoneNumber;
  final String accessToken;
  final String? mealType;

  @override
  State<FoodReviewScreen> createState() => _FoodReviewScreenState();
}

class _FoodReviewScreenState extends State<FoodReviewScreen> {
  final FoodLogApiClient _api = FoodLogApiClient();
  late FoodAnalysis _analysis = widget.analysis;
  bool _busy = false;

  Future<void> _fix(
    String action, {
    String? itemId,
    String? foodCode,
    String? correctedName,
    double? grams,
    double? quantity,
    String? unit,
  }) async {
    setState(() => _busy = true);
    try {
      final updated = await _api.fix(
        phoneNumber: widget.phoneNumber,
        accessToken: widget.accessToken,
        analysisId: _analysis.analysisId!,
        action: action,
        itemId: itemId,
        foodCode: foodCode,
        correctedName: correctedName,
        grams: grams,
        quantity: quantity,
        unit: unit,
      );
      if (mounted) setState(() => _analysis = updated);
    } on FoodLogApiException catch (e) {
      _toast(e.message);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _confirm() async {
    setState(() => _busy = true);
    try {
      await _api.confirm(
        phoneNumber: widget.phoneNumber,
        accessToken: widget.accessToken,
        analysisId: _analysis.analysisId!,
        mealType: widget.mealType,
      );
      if (mounted) Navigator.of(context).pop(true);
    } on FoodLogApiException catch (e) {
      _toast(e.message);
      if (mounted) setState(() => _busy = false);
    }
  }

  void _toast(String message) {
    if (!mounted) return;
    ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(message)));
  }

  Future<void> _editAmount(FoodItem item) async {
    final result = await showModalBottomSheet<(double, FoodUnitOption)>(
      context: context,
      backgroundColor: AppColors.surface,
      isScrollControlled: true,
      builder: (_) => _AmountSheet(item: item),
    );
    if (result == null) return;
    final (quantity, option) = result;
    await _fix(
      'set_amount',
      itemId: item.itemId,
      quantity: quantity,
      unit: option.unit,
    );
  }

  Future<void> _replace(FoodItem? item) async {
    final choice = await showModalBottomSheet<_Replacement>(
      context: context,
      backgroundColor: AppColors.surface,
      isScrollControlled: true,
      builder: (_) => _ReplaceSheet(
        item: item,
        search: (query) async => _analysis.databaseEnabled
            ? _api.search(
                phoneNumber: widget.phoneNumber,
                accessToken: widget.accessToken,
                query: query,
              )
            : const <FoodChoice>[],
      ),
    );
    if (choice == null) return;
    await _fix(
      item == null ? 'add' : 'replace',
      itemId: item?.itemId,
      foodCode: choice.foodCode,
      correctedName: choice.typedName,
    );
  }

  @override
  Widget build(BuildContext context) {
    final totals = _analysis.totals;
    return Scaffold(
      backgroundColor: AppColors.background,
      appBar: AppBar(
        backgroundColor: AppColors.background,
        foregroundColor: AppColors.textPrimary,
        title: const Text('Is this correct?'),
      ),
      body: AbsorbPointer(
        absorbing: _busy,
        child: ListView(
          padding: const EdgeInsets.all(AppSpacing.md),
          children: [
            if (_busy) const LinearProgressIndicator(),
            if (_analysis.transcript.isNotEmpty)
              Text(
                'Heard: "${_analysis.transcript}"',
                style: const TextStyle(color: AppColors.textSecondary),
              ),
            ..._analysis.items.map(
              (item) => _ItemCard(
                item: item,
                onAmount: () => _editAmount(item),
                onReplace: () => _replace(item),
                onUseEstimate: item.aiEstimate == null
                    ? null
                    : () => _fix('use_estimate', itemId: item.itemId),
                onRemove: () => _fix('remove', itemId: item.itemId),
              ),
            ),
            TextButton.icon(
              onPressed: () => _replace(null),
              icon: const Icon(Icons.add, color: AppColors.accent),
              label: const Text(
                'Add a missed item',
                style: TextStyle(color: AppColors.textPrimary),
              ),
            ),
            const SizedBox(height: AppSpacing.sm),
            _MacroRow(title: 'Meal total', nutrients: totals),
            const SizedBox(height: AppSpacing.lg),
            FilledButton(
              style: FilledButton.styleFrom(
                backgroundColor: AppColors.accent,
                foregroundColor: Colors.black,
                minimumSize: const Size.fromHeight(50),
              ),
              onPressed: _analysis.items.isEmpty ? null : _confirm,
              child: const Text('Looks right, save meal'),
            ),
          ],
        ),
      ),
    );
  }
}

String _fmt(double value, [int digits = 0]) =>
    value.toStringAsFixed(digits).replaceAll(RegExp(r'\.0+$'), '');

class _ItemCard extends StatelessWidget {
  const _ItemCard({
    required this.item,
    required this.onAmount,
    required this.onReplace,
    required this.onUseEstimate,
    required this.onRemove,
  });

  final FoodItem item;
  final VoidCallback onAmount;
  final VoidCallback onReplace;
  final VoidCallback? onUseEstimate;
  final VoidCallback onRemove;

  @override
  Widget build(BuildContext context) {
    final subtitle = [
      if (item.name != item.aiName) 'AI saw: ${item.aiName}',
      '${_fmt(item.quantity, 1)} ${item.unit} · ${_fmt(item.grams)} g'
          '${item.gramsSource == 'your_usual' ? ' (your usual)' : ''}',
    ].join(' · ');

    return Container(
      margin: const EdgeInsets.only(top: AppSpacing.sm),
      padding: const EdgeInsets.all(AppSpacing.sm),
      decoration: BoxDecoration(
        color: AppColors.surface,
        borderRadius: BorderRadius.circular(14),
        border: Border.all(
          color: item.needsReview ? AppColors.accent : AppColors.surfaceAlt,
        ),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
            item.name,
            style: const TextStyle(
              color: AppColors.textPrimary,
              fontSize: 16,
              fontWeight: FontWeight.w700,
            ),
          ),
          Text(subtitle, style: const TextStyle(color: AppColors.textSecondary, fontSize: 12.5)),
          const SizedBox(height: AppSpacing.xs),
          Text(
            item.isVerified
                ? '✓ Verified · ${item.dataSource ?? 'database'}'
                : '≈ AI estimate',
            style: TextStyle(
              color: item.isVerified ? Colors.greenAccent : AppColors.accent,
              fontSize: 12,
              fontWeight: FontWeight.w700,
            ),
          ),
          ...item.reviewMessages.map(
            (m) => Text('⚠ $m', style: const TextStyle(color: AppColors.accent, fontSize: 12.5)),
          ),
          if (item.note.isNotEmpty)
            Text('ℹ ${item.note}', style: const TextStyle(color: AppColors.textSecondary, fontSize: 12.5)),
          const SizedBox(height: AppSpacing.xs),
          _MacroRow(nutrients: item.nutrients),
          if (item.aiEstimate != null)
            Text(
              'AI thinks ${_fmt(item.aiEstimate!['energy_kcal'] ?? 0)} kcal · '
              'database ${_fmt(item.kcal)} kcal',
              style: const TextStyle(color: AppColors.textSecondary, fontSize: 12),
            ),
          Wrap(
            spacing: AppSpacing.xs,
            children: [
              TextButton(onPressed: onAmount, child: const Text('Change amount')),
              TextButton(onPressed: onReplace, child: const Text('Not this?')),
              if (onUseEstimate != null)
                TextButton(onPressed: onUseEstimate, child: const Text('Use AI estimate')),
              TextButton(onPressed: onRemove, child: const Text('Remove')),
            ],
          ),
        ],
      ),
    );
  }
}

class _MacroRow extends StatelessWidget {
  const _MacroRow({required this.nutrients, this.title});

  final Map<String, double> nutrients;
  final String? title;

  @override
  Widget build(BuildContext context) {
    Widget tile(String key, String label, [int digits = 1]) => Expanded(
      child: Container(
        margin: const EdgeInsets.all(2),
        padding: const EdgeInsets.symmetric(vertical: 8),
        decoration: BoxDecoration(
          color: AppColors.surfaceAlt,
          borderRadius: BorderRadius.circular(10),
        ),
        child: Column(
          children: [
            Text(
              _fmt(nutrients[key] ?? 0, digits),
              style: const TextStyle(
                color: AppColors.textPrimary,
                fontWeight: FontWeight.w700,
                fontSize: 16,
              ),
            ),
            Text(label, style: const TextStyle(color: AppColors.textSecondary, fontSize: 11)),
          ],
        ),
      ),
    );

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        if (title != null)
          Text(title!, style: const TextStyle(color: AppColors.textPrimary, fontWeight: FontWeight.w700)),
        Row(
          children: [
            tile('energy_kcal', 'kcal', 0),
            tile('protein_g', 'protein g'),
            tile('carb_g', 'carbs g'),
            tile('fat_g', 'fat g'),
          ],
        ),
      ],
    );
  }
}

class _AmountSheet extends StatefulWidget {
  const _AmountSheet({required this.item});

  final FoodItem item;

  @override
  State<_AmountSheet> createState() => _AmountSheetState();
}

class _AmountSheetState extends State<_AmountSheet> {
  late double _quantity = widget.item.quantity;
  late FoodUnitOption _option = widget.item.unitOptions.firstWhere(
    (o) => o.unit == widget.item.unit,
    orElse: () => widget.item.unitOptions.first,
  );

  @override
  Widget build(BuildContext context) {
    final step = _option.unit == 'g' ? 10.0 : 0.5;
    return Padding(
      padding: EdgeInsets.fromLTRB(
        AppSpacing.md,
        AppSpacing.md,
        AppSpacing.md,
        AppSpacing.md + MediaQuery.of(context).viewInsets.bottom,
      ),
      child: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          Text(widget.item.name, style: const TextStyle(color: AppColors.textPrimary, fontSize: 16)),
          Row(
            mainAxisAlignment: MainAxisAlignment.center,
            children: [
              IconButton(
                icon: const Icon(Icons.remove_circle_outline, color: AppColors.accent),
                onPressed: _quantity > step ? () => setState(() => _quantity -= step) : null,
              ),
              Text(
                _fmt(_quantity, 1),
                style: const TextStyle(color: AppColors.textPrimary, fontSize: 24),
              ),
              IconButton(
                icon: const Icon(Icons.add_circle_outline, color: AppColors.accent),
                onPressed: () => setState(() => _quantity += step),
              ),
            ],
          ),
          Wrap(
            spacing: AppSpacing.xs,
            children: widget.item.unitOptions
                .map(
                  (o) => ChoiceChip(
                    label: Text('${o.unit} (${_fmt(o.gramsPerUnit)} g)${o.yours ? ' · yours' : ''}'),
                    selected: o.unit == _option.unit,
                    onSelected: (_) => setState(() {
                      // Keep the same weight when switching units.
                      final grams = _quantity * _option.gramsPerUnit;
                      _option = o;
                      _quantity = double.parse((grams / o.gramsPerUnit).toStringAsFixed(1));
                    }),
                  ),
                )
                .toList(),
          ),
          Text(
            '= ${_fmt(_quantity * _option.gramsPerUnit)} g',
            style: const TextStyle(color: AppColors.textSecondary),
          ),
          const SizedBox(height: AppSpacing.md),
          FilledButton(
            onPressed: () => Navigator.of(context).pop((_quantity, _option)),
            child: const Text('Save amount'),
          ),
        ],
      ),
    );
  }
}

class _Replacement {
  const _Replacement({this.foodCode, this.typedName});

  final String? foodCode;
  final String? typedName;
}

class _ReplaceSheet extends StatefulWidget {
  const _ReplaceSheet({required this.item, required this.search});

  final FoodItem? item; // null = adding a missed item
  final Future<List<FoodChoice>> Function(String query) search;

  @override
  State<_ReplaceSheet> createState() => _ReplaceSheetState();
}

class _ReplaceSheetState extends State<_ReplaceSheet> {
  final TextEditingController _controller = TextEditingController();
  List<FoodChoice> _results = const [];
  Timer? _debounce;

  @override
  void dispose() {
    _debounce?.cancel();
    _controller.dispose();
    super.dispose();
  }

  void _onChanged(String query) {
    _debounce?.cancel();
    if (query.trim().length < 2) return;
    _debounce = Timer(const Duration(milliseconds: 300), () async {
      try {
        final results = await widget.search(query);
        if (mounted) setState(() => _results = results);
      } on FoodLogApiException {
        // Search is optional; the user can still submit the typed name.
      }
    });
  }

  Widget _choice(FoodChoice c) => ListTile(
    dense: true,
    title: Text(c.foodName, style: const TextStyle(color: AppColors.textPrimary)),
    subtitle: c.kcalPer100g == null
        ? null
        : Text(
            '${_fmt(c.kcalPer100g!)} kcal / 100 g${c.suspect ? ' · value under review' : ''}',
            style: const TextStyle(color: AppColors.textSecondary),
          ),
    onTap: () => Navigator.of(context).pop(_Replacement(foodCode: c.foodCode)),
  );

  @override
  Widget build(BuildContext context) {
    final alternatives = widget.item?.alternatives ?? const <FoodChoice>[];
    return Padding(
      padding: EdgeInsets.fromLTRB(
        AppSpacing.md,
        AppSpacing.md,
        AppSpacing.md,
        AppSpacing.md + MediaQuery.of(context).viewInsets.bottom,
      ),
      child: ConstrainedBox(
        constraints: BoxConstraints(maxHeight: MediaQuery.of(context).size.height * 0.75),
        child: ListView(
          shrinkWrap: true,
          children: [
            Text(
              widget.item == null ? 'What did we miss?' : 'What is it really?',
              style: const TextStyle(color: AppColors.textPrimary, fontSize: 16, fontWeight: FontWeight.w700),
            ),
            if (alternatives.isNotEmpty) ...[
              const SizedBox(height: AppSpacing.xs),
              const Text('Maybe:', style: TextStyle(color: AppColors.textSecondary)),
              ...alternatives.map(_choice),
            ],
            TextField(
              controller: _controller,
              autofocus: alternatives.isEmpty,
              style: const TextStyle(color: AppColors.textPrimary),
              decoration: const InputDecoration(
                hintText: 'Type the dish, e.g. methi thepla',
                hintStyle: TextStyle(color: AppColors.textSecondary),
              ),
              onChanged: _onChanged,
              onSubmitted: (value) {
                if (value.trim().isNotEmpty) {
                  Navigator.of(context).pop(_Replacement(typedName: value.trim()));
                }
              },
            ),
            ..._results.map(_choice),
            const SizedBox(height: AppSpacing.sm),
            FilledButton(
              onPressed: () {
                final value = _controller.text.trim();
                if (value.isNotEmpty) {
                  Navigator.of(context).pop(_Replacement(typedName: value));
                }
              },
              child: const Text('Use what I typed'),
            ),
          ],
        ),
      ),
    );
  }
}

package SRP;

public abstract class Symptom {

	int covidImpact;
	int severityIndex;
	String name;
	Double affectedDays;

	public Symptom(int covidImpact, int severityIndex, String name, Double affectedDays) {
		this.covidImpact = covidImpact;
		this.severityIndex = severityIndex;
		this.name = name;
		this.affectedDays = affectedDays;
	}

	public abstract double getProbabilityCovid19();

	protected abstract double getSeverityIndex();

	protected abstract int getCovidImpact();

}
